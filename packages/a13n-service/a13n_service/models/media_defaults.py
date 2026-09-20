"""Workspace-owned defaults for native-first file-media understanding."""

from __future__ import annotations

from datetime import datetime

from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.etags import etag_matches
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.storage import short_session, transaction

from .domain import Model, ModelKey
from .models import MediaUnderstandingDefaultsRecord, ModelProviderRecord, ModelRecord
from .service_common import ModelError, audit_record, authorize_models

MEDIA_KINDS: tuple[NativeInputMediaKind, ...] = ("image", "video", "audio")


class MediaUnderstandingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image: ModelKey | None = None
    video: ModelKey | None = None
    audio: ModelKey | None = None

    def selections(self) -> dict[NativeInputMediaKind, str]:
        values: dict[NativeInputMediaKind, str | None] = {
            "image": self.image,
            "video": self.video,
            "audio": self.audio,
        }
        return {kind: key for kind, key in values.items() if key is not None}


class MediaUnderstandingDefaults(MediaUnderstandingSelection):
    workspace_id: str
    version: int = Field(ge=0)

    def etag(self) -> str:
        return f'"{digest_request(self.model_dump(mode="json"))}"'


def require_media_capability(model: Model, kind: NativeInputMediaKind) -> None:
    if f"{kind}_understanding" not in model.declarations.capabilities:
        raise ModelError(
            "model_media_capability_required",
            f"The selected Model must declare {kind}_understanding.",
            category=ErrorCategory.invalid_request,
            details={"kind": kind, "model_key": model.key},
        )


async def read_media_defaults(session: AsyncSession, workspace_id: str) -> MediaUnderstandingDefaults:
    row = await session.get(MediaUnderstandingDefaultsRecord, workspace_id)
    if row is None:
        return MediaUnderstandingDefaults(workspace_id=workspace_id, version=0)
    ids = row.selections()
    keys = {
        model_id: key
        for model_id, key in (
            await session.execute(select(ModelRecord.id, ModelRecord.key).where(ModelRecord.id.in_(ids.values())))
        ).all()
    }
    return MediaUnderstandingDefaults(
        workspace_id=workspace_id,
        version=row.version,
        **{kind: keys[model_id] for kind, model_id in ids.items()},
    )


async def get_media_defaults(
    sessions: async_sessionmaker[AsyncSession], *, actor: AuthenticatedActor, workspace_id: str
) -> MediaUnderstandingDefaults:
    async with short_session(sessions) as session:
        await authorize_models(session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read)
        return await read_media_defaults(session, workspace_id)


async def replace_media_defaults(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    request: MediaUnderstandingSelection,
    if_match: str,
    now: datetime,
) -> MediaUnderstandingDefaults:
    async with transaction(sessions) as session:
        scope = await authorize_models(
            session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
        )
        # Serialize the initial insert as well as updates without creating a read-side row.
        await session.scalar(select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id).with_for_update())
        current = await read_media_defaults(session, workspace_id)
        if not etag_matches(if_match, current.etag()):
            raise ModelError(
                "precondition_failed",
                "The defaults changed after they were read.",
                category=ErrorCategory.stale_version,
                details={"current_etag": current.etag()},
            )
        selected: dict[NativeInputMediaKind, str] = {}
        for kind, key in request.selections().items():
            result = (
                await session.execute(
                    select(ModelRecord, ModelProviderRecord)
                    .join(ModelProviderRecord, ModelProviderRecord.id == ModelRecord.provider_id)
                    .where(
                        ModelRecord.organization_id == scope.organization_id,
                        visible_workspace(ModelRecord.workspace_id, workspace_id),
                        ModelRecord.normalized_key == key,
                    )
                    .with_for_update(read=True)
                )
            ).one_or_none()
            if result is None:
                raise ModelError("model_not_found", "The Model was not found.", category=ErrorCategory.not_found)
            model, provider = result
            if not model.enabled or not provider.enabled:
                raise ModelError(
                    "model_disabled", "The Model or its Provider is disabled.", category=ErrorCategory.conflict
                )
            require_media_capability(model.to_resource(), kind)
            selected[kind] = model.id
        row = await session.get(MediaUnderstandingDefaultsRecord, workspace_id)
        if row is None:
            row = MediaUnderstandingDefaultsRecord(workspace_id=workspace_id, version=0)
            session.add(row)
        row.image_model_id = selected.get("image")
        row.video_model_id = selected.get("video")
        row.audio_model_id = selected.get("audio")
        row.version += 1
        session.add(
            audit_record(
                actor=actor,
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                resource_type="media_understanding_defaults",
                resource_id=workspace_id,
                action="media_understanding_defaults.update",
                now=now,
            )
        )
        await session.flush()
        return await read_media_defaults(session, workspace_id)
