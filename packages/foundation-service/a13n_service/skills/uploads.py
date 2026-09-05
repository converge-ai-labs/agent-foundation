"""Staged ZIP receipt application service."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import SkillUploadReceipt, new_skill_upload_id
from .errors import SkillError
from .models import SkillUploadRecord
from .objects import SkillPackageStore, SkillPackageStoreError
from .package import NormalizedSkillPackage, SkillPackageError, normalize_skill_zip
from .support import (
    IdempotencyIdentity,
    IdempotencyScope,
    ReplayResult,
    authorize_skill_workspace,
    idempotency_identity,
    is_idempotency_race,
    load_replay,
    new_replay_evidence,
)

UPLOAD_LIFETIME = timedelta(hours=24)
_STAGE_OPERATION = "skill_upload.stage"


class SkillUploadService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        packages: SkillPackageStore,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._clock = clock or utc_now

    async def stage(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        archive: bytes,
    ) -> ReplayResult[SkillUploadReceipt]:
        identity = await asyncio.to_thread(idempotency_identity, idempotency_key, archive)
        replay, organization_id = await self._preauthorize_and_replay(
            actor=actor,
            workspace_id=workspace_id,
            identity=identity,
        )
        if replay is not None:
            return replay
        try:
            package = await asyncio.to_thread(normalize_skill_zip, archive)
        except SkillPackageError as error:
            raise SkillError(error.code, str(error), status_code=400) from error
        await self._publish_package(
            organization_id=organization_id,
            workspace_id=workspace_id,
            package=package,
        )
        now = self._clock()
        upload_id = new_skill_upload_id()
        receipt = SkillUploadReceipt(
            upload_id=upload_id,
            workspace_id=workspace_id,
            archive_sha256=identity.request_digest,
            manifest=package.manifest,
            expires_at=now + UPLOAD_LIFETIME,
            consumed_by_revision_id=None,
        )
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_skill_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.skill_create,
                )
                scope = _stage_scope(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    identity=identity,
                )
                replay = await load_replay(
                    session,
                    scope=scope,
                    now=now,
                    response_model=SkillUploadReceipt,
                )
                if replay is not None:
                    return replay
                session.add(
                    SkillUploadRecord(
                        id=upload_id,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        uploader_type=actor.principal.principal_type.value,
                        uploader_id=actor.principal.principal_id,
                        archive_sha256=receipt.archive_sha256,
                        manifest=package.manifest.model_dump(mode="json"),
                        created_at=now,
                        expires_at=receipt.expires_at,
                        consumed_by_revision_id=None,
                    )
                )
                session.add(
                    new_replay_evidence(
                        scope=scope,
                        response=receipt,
                        status_code=201,
                        now=now,
                    )
                )
                await session.flush()
                return ReplayResult(result=receipt, status_code=201)
        except IntegrityError as error:
            if not is_idempotency_race(error):
                raise
            return await self._require_committed_replay(
                actor=actor,
                workspace_id=workspace_id,
                identity=identity,
            )

    async def get(self, *, actor: AuthenticatedActor, upload_id: str) -> SkillUploadReceipt:
        workspace_id = actor.workspace_id
        async with transaction(self._sessions) as session:
            await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_create,
                concealed_code="skill_upload_not_found",
            )
            record = await session.scalar(_owned_upload_query(actor=actor, upload_id=upload_id))
            if record is None:
                raise _upload_not_found()
            if assume_utc(record.expires_at) <= assume_utc(self._clock()):
                raise SkillError(
                    "skill_upload_expired",
                    "The staged Skill upload has expired.",
                    status_code=409,
                )
            return record.to_resource()

    async def delete(self, *, actor: AuthenticatedActor, upload_id: str) -> None:
        workspace_id = actor.workspace_id
        async with transaction(self._sessions) as session:
            await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_create,
                concealed_code="skill_upload_not_found",
            )
            record = await session.scalar(_owned_upload_query(actor=actor, upload_id=upload_id).with_for_update())
            if record is None:
                raise _upload_not_found()
            if record.consumed_by_revision_id is not None:
                raise SkillError(
                    "skill_upload_consumed",
                    "The staged Skill upload has already been consumed.",
                    status_code=409,
                )
            await session.delete(record)

    async def _preauthorize_and_replay(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        identity: IdempotencyIdentity,
    ) -> tuple[ReplayResult[SkillUploadReceipt] | None, str]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.skill_create,
            )
            scope = _stage_scope(
                actor=actor,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                identity=identity,
            )
            replay = await load_replay(
                session,
                scope=scope,
                now=now,
                response_model=SkillUploadReceipt,
            )
            return replay, workspace.organization_id

    async def _require_committed_replay(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        identity: IdempotencyIdentity,
    ) -> ReplayResult[SkillUploadReceipt]:
        replay, _organization_id = await self._preauthorize_and_replay(
            actor=actor,
            workspace_id=workspace_id,
            identity=identity,
        )
        if replay is None:
            raise SkillError(
                "idempotency_conflict",
                "The idempotent Skill upload could not be reconciled.",
                status_code=409,
            )
        return replay

    async def _publish_package(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        package: NormalizedSkillPackage,
    ) -> None:
        try:
            await self._packages.publish(
                organization_id=organization_id,
                workspace_id=workspace_id,
                package=package,
            )
        except SkillPackageStoreError as error:
            raise SkillError(
                error.code,
                str(error),
                status_code=503 if error.code == "skill_package_unavailable" else 400,
            ) from error


def _owned_upload_query(*, actor: AuthenticatedActor, upload_id: str):
    return select(SkillUploadRecord).where(
        SkillUploadRecord.id == upload_id,
        SkillUploadRecord.workspace_id == actor.workspace_id,
        SkillUploadRecord.uploader_type == actor.principal.principal_type.value,
        SkillUploadRecord.uploader_id == actor.principal.principal_id,
    )


def _stage_scope(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    identity: IdempotencyIdentity,
) -> IdempotencyScope:
    return IdempotencyScope(
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        operation=_STAGE_OPERATION,
        resource_scope_id=workspace_id,
        identity=identity,
    )


def _upload_not_found() -> SkillError:
    return SkillError(
        "skill_upload_not_found",
        "The staged Skill upload was not found.",
        status_code=404,
    )
