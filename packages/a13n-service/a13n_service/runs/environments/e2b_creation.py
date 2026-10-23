"""User-visible automatic E2B/Modal/Daytona/Vercel creation progress and fenced retry; no cloud I/O on reads."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments import daytona_snapshots, e2b_templates, lifecycle, modal_images, vercel_snapshots
from a13n_service.runs.environments.adapters import provider_identity
from a13n_service.runs.environments.schemas import EnvironmentView, Handle
from a13n_service.runs.environments.service import _audit, _find
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, authorize


class CreationProgress(BaseModel):
    stage: Literal["preparing", "building", "starting", "ready", "failed", "inactive"]
    started_at: datetime
    observed_at: datetime
    ended_at: datetime | None = None
    retryable: bool = False


def _automatic(row: EnvironmentRow, handle: Handle | None) -> bool:
    return bool(
        handle
        and (
            handle.e2b_desktop_template
            or handle.modal_desktop_image
            or handle.daytona_desktop_snapshot
            or handle.vercel_desktop_snapshot
            or (
                row.provider_identity
                and (
                    (row.provider_identity.get("type") == "e2b" and handle.recipe.get("template") == "auto")
                    or (row.provider_identity.get("type") == "modal" and handle.recipe.get("image") == "auto")
                    or (row.provider_identity.get("type") == "daytona" and handle.recipe.get("snapshot") == "auto")
                    or (row.provider_identity.get("type") == "vercel" and handle.recipe.get("snapshot") == "auto")
                )
                and handle.recipe.get("desktop")
            )
        )
    )


def _retryable(row: EnvironmentRow) -> bool:
    return row.status == "creating" and row.failure is not None and lifecycle.settled(row)


async def progress(runtime: Runtime, actor: Principal, workspace_id: str, identity: str) -> CreationProgress | None:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _find(session, scope, identity)
        handle = Handle.model_validate(row.handle) if row.handle else None
        if not _automatic(row, handle):
            return None
        assert handle is not None
        stage: Literal["preparing", "building", "starting", "ready", "failed", "inactive"] = "preparing"
        ended = None
        if row.status == "ready":
            stage = "ready"
        elif row.status != "creating":
            stage = "inactive"
        elif row.failure and row.failure["permanent"]:
            stage, ended = "failed", row.failure["at"]
        elif handle.modal_desktop_image:
            image = await session.get(modal_images.ModalDesktopImageRow, handle.modal_desktop_image)
            if image and image.workspace_id == row.workspace_id and image.provider_id == row.provider_id:
                if handle.recipe.get("image") != "auto" or image.phase == "ready":
                    stage = "starting"
                elif image.phase == "building":
                    stage = "building"
                elif image.phase == "failed":
                    stage, ended = "failed", image.updated_at
        elif handle.daytona_desktop_snapshot:
            snapshot = await session.get(daytona_snapshots.DaytonaDesktopSnapshotRow, handle.daytona_desktop_snapshot)
            if snapshot and snapshot.workspace_id == row.workspace_id and snapshot.provider_id == row.provider_id:
                if handle.recipe.get("snapshot") != "auto" or snapshot.phase == "ready":
                    stage = "starting"
                elif snapshot.phase in {"submitting", "building"}:
                    stage = "building"
                elif snapshot.phase in {"failed", "cleanup"}:
                    stage, ended = "failed", snapshot.updated_at
        elif handle.vercel_desktop_snapshot:
            snapshot = await session.get(vercel_snapshots.VercelDesktopSnapshotRow, handle.vercel_desktop_snapshot)
            if snapshot and snapshot.workspace_id == row.workspace_id and snapshot.provider_id == row.provider_id:
                if handle.recipe.get("snapshot") != "auto" or snapshot.phase == "ready":
                    stage = "starting"
                elif snapshot.phase in {"allocating", "installing", "snapshotting", "cleanup_ready"}:
                    stage = "building"
                elif snapshot.phase in {"failed", "cleanup"}:
                    stage, ended = "failed", snapshot.updated_at
        elif handle.recipe.get("template") != "auto":
            stage = "starting"
        elif handle.e2b_desktop_template:
            build = await session.get(e2b_templates.E2BDesktopTemplateRow, handle.e2b_desktop_template)
            if build and build.workspace_id == row.workspace_id and build.provider_id == row.provider_id:
                if build.phase in {"submitting", "building"}:
                    stage = "building"
                elif build.phase == "ready":
                    stage = "starting"
                elif build.phase in {"failed", "cleanup"}:
                    stage, ended = "failed", build.updated_at
        return CreationProgress(
            stage=stage,
            observed_at=await now(session),
            started_at=row.operation_started_at or row.created_at,
            ended_at=ended,
            retryable=_retryable(row),
        )


async def retry(
    runtime: Runtime, actor: Principal, workspace_id: str, identity: str, *, if_match: str | None
) -> EnvironmentView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        authorize(actor, scope, "run")
        row = await _find(session, scope, identity, lock=True)
        require_match(if_match, row.id, row.version)
        handle = Handle.model_validate(row.handle) if row.handle else None
        if not _automatic(row, handle) or not _retryable(row):
            raise conflict("environment", identity, "creation_retry_unavailable")
        if await session.scalar(select(lifecycle.in_use(row.id))):
            raise conflict("environment", identity, "in_use")
        assert handle is not None and row.provider_id is not None
        provider = await resolve_provider(session, actor, EnvironmentProviderRow, scope, row.provider_id)
        if row.provider_identity != provider_identity(runtime.registry, provider):
            raise conflict("environment", identity, "provider_identity_changed")
        if handle.recipe.get("template") == "auto" and handle.e2b_desktop_template:
            build = await session.get(e2b_templates.E2BDesktopTemplateRow, handle.e2b_desktop_template)
            if build is not None and build.phase == "failed":
                # Only a confirmed failed/cleaned build may be replaced. Ambiguous calls keep their identity.
                key = await e2b_templates.bind(runtime, session, row, provider)
                row.handle = handle.model_copy(update={"e2b_desktop_template": key}).model_dump(mode="json")
        if handle.recipe.get("image") == "auto" and handle.modal_desktop_image:
            image = await session.get(modal_images.ModalDesktopImageRow, handle.modal_desktop_image)
            if image is not None and image.phase == "failed":
                key = await modal_images.bind(runtime, session, row, provider)
                row.handle = handle.model_copy(update={"modal_desktop_image": key}).model_dump(mode="json")
        if handle.recipe.get("snapshot") == "auto" and handle.daytona_desktop_snapshot:
            snapshot = await session.get(daytona_snapshots.DaytonaDesktopSnapshotRow, handle.daytona_desktop_snapshot)
            if snapshot is not None and snapshot.phase == "failed":
                key = await daytona_snapshots.bind(runtime, session, row, provider)
                row.handle = handle.model_copy(update={"daytona_desktop_snapshot": key}).model_dump(mode="json")
        if handle.recipe.get("snapshot") == "auto" and handle.vercel_desktop_snapshot:
            snapshot = await session.get(vercel_snapshots.VercelDesktopSnapshotRow, handle.vercel_desktop_snapshot)
            if snapshot is not None and snapshot.phase == "failed":
                key = await vercel_snapshots.bind(runtime, session, row, provider)
                row.handle = handle.model_copy(update={"vercel_desktop_snapshot": key}).model_dump(mode="json")
        await lifecycle.begin(session, row, "creating")
        await session.flush()
        _audit(session, actor, row, "retry_creation")
        return EnvironmentView.model_validate(row)
