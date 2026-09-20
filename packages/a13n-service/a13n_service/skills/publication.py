"""Atomic creation and immutable revision publication for managed Skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import is_evidence_unique_race
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.labels import Labels
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import (
    CreateSkillRequest,
    CreateSkillRevisionRequest,
    SkillPublicationReceipt,
    new_skill_id,
    new_skill_revision_id,
)
from .errors import SkillError, skill_key_conflict, skill_key_mismatch, skill_version_conflict
from .models import (
    SkillRecord,
    SkillRevisionRecord,
    SkillUploadRecord,
)
from .package import skill_package_object_key
from .persistence import (
    lock_active_skill,
    require_owned_upload,
    require_revision,
    require_upload_manifest,
)
from .sources import PreparedSkillSource, SkillSourcePreparer
from .support import (
    IdempotencyIdentity,
    IdempotencyScope,
    ReplayResult,
    authorize_skill_workspace,
    idempotency_identity,
    is_skill_key_race,
    load_replay,
    new_replay_evidence,
    record_failed_skill_attempt,
    skill_audit_record,
    skill_request_key,
)

_CREATE_OPERATION = "skill.create"
_REVISION_OPERATION = "skill.revision.publish"


@dataclass(frozen=True, slots=True)
class _ReplayCommand:
    actor: AuthenticatedActor
    workspace_id: str
    action: WorkspaceAction
    operation: str
    resource_scope_id: str
    identity: IdempotencyIdentity

    def scope(self, organization_id: str) -> IdempotencyScope:
        return IdempotencyScope(
            actor=self.actor,
            organization_id=organization_id,
            workspace_id=self.workspace_id,
            operation=self.operation,
            resource_scope_id=self.resource_scope_id,
            identity=self.identity,
        )


@dataclass(frozen=True, slots=True)
class _PublicationContext:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    skill_id: str
    now: datetime
    labels: Labels = field(default_factory=dict)


class SkillPublicationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sources: SkillSourcePreparer,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = sessions
        self._sources = sources
        self._clock = clock or utc_now

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateSkillRequest,
        idempotency_key: str,
    ) -> ReplayResult[SkillPublicationReceipt]:
        try:
            return await self._create(
                actor=actor,
                workspace_id=workspace_id,
                request=request,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            await self._audit_failure(
                error,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=None,
                action="skill.create",
            )
            raise

    async def _create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateSkillRequest,
        idempotency_key: str,
    ) -> ReplayResult[SkillPublicationReceipt]:
        command = _ReplayCommand(
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.skill_create,
            operation=_CREATE_OPERATION,
            resource_scope_id=workspace_id,
            identity=idempotency_identity(idempotency_key),
        )
        replay, organization_id = await self._preauthorize_replay(command)
        if replay is not None:
            return replay
        prepared = await self._sources.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.skill_create,
            source=request.source,
        )
        now = self._clock()
        skill_id = new_skill_id()
        revision_id = new_skill_revision_id()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_skill_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.skill_create,
                )
                replay = await load_replay(
                    session,
                    scope=command.scope(workspace.organization_id),
                    now=now,
                    response_model=SkillPublicationReceipt,
                )
                if replay is not None:
                    return replay
                upload = await self._lock_upload(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    prepared=prepared,
                    now=now,
                )
                context = _PublicationContext(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    skill_id=skill_id,
                    now=now,
                    labels=request.labels,
                )
                skill = _new_skill_record(
                    context,
                    key=prepared.package.manifest.skill_name,
                    name=request.name or prepared.package.manifest.skill_name,
                    revision_id=revision_id,
                )
                revision = _new_revision_record(
                    context,
                    revision_id=revision_id,
                    version=1,
                    prepared=prepared,
                )
                skill.request_key = skill_request_key(command.scope(workspace.organization_id))
                session.add_all((skill, revision))
                await session.flush((skill, revision))
                if upload is not None:
                    upload.consumed_by_revision_id = revision_id
                result = SkillPublicationReceipt(
                    skill=skill.to_resource(),
                    revision=revision.to_resource(),
                    outcome="published",
                )
                session.add(
                    skill_audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        skill_id=skill_id,
                        action="skill.create",
                        now=now,
                        details={"selected_revision_id": revision_id, "source_kind": request.source.kind},
                    )
                )
                await session.flush()
                return ReplayResult(result=result, created=True)
        except IntegrityError as error:
            replay, _organization_id = await self._preauthorize_replay(command)
            if replay is not None:
                return replay
            if is_skill_key_race(error):
                raise skill_key_conflict() from error
            raise

    async def publish_revision(
        self,
        *,
        actor: AuthenticatedActor,
        skill_id: str,
        request: CreateSkillRevisionRequest,
        idempotency_key: str,
    ) -> ReplayResult[SkillPublicationReceipt]:
        workspace_id = actor.workspace_id
        try:
            return await self._publish_revision(
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                request=request,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            await self._audit_failure(
                error,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action="skill.revision.publish",
            )
            raise

    async def _publish_revision(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str,
        request: CreateSkillRevisionRequest,
        idempotency_key: str,
    ) -> ReplayResult[SkillPublicationReceipt]:
        command = _ReplayCommand(
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.skill_revision_publish,
            operation=_REVISION_OPERATION,
            resource_scope_id=skill_id,
            identity=idempotency_identity(idempotency_key),
        )
        replay, organization_id = await self._preauthorize_replay(command)
        if replay is not None:
            return replay
        prepared = await self._sources.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.skill_revision_publish,
            source=request.source,
        )
        try:
            return await self._commit_revision(
                command=command,
                request=request,
                prepared=prepared,
            )
        except IntegrityError as error:
            if not is_evidence_unique_race(error):
                raise
            return await self._require_replay(command)

    async def _commit_revision(
        self,
        *,
        command: _ReplayCommand,
        request: CreateSkillRevisionRequest,
        prepared: PreparedSkillSource,
    ) -> ReplayResult[SkillPublicationReceipt]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=command.actor,
                workspace_id=command.workspace_id,
                action=command.action,
                concealed_code="skill_not_found",
            )
            scope = command.scope(workspace.organization_id)
            replay = await load_replay(
                session,
                scope=scope,
                now=now,
                response_model=SkillPublicationReceipt,
            )
            if replay is not None:
                return replay
            locked = await lock_active_skill(
                session,
                organization_id=workspace.organization_id,
                workspace_id=command.workspace_id,
                skill_id=command.resource_scope_id,
            )
            replay = await load_replay(
                session,
                scope=scope,
                now=now,
                response_model=SkillPublicationReceipt,
            )
            if replay is not None:
                return replay
            if locked.version != request.expected_version:
                raise skill_version_conflict(locked.version)
            if prepared.package.manifest.skill_name != locked.key:
                raise skill_key_mismatch()
            default = await require_revision(
                session,
                organization_id=workspace.organization_id,
                workspace_id=command.workspace_id,
                revision_id=locked.default_revision_id,
            )
            upload = await self._lock_upload(
                session,
                actor=command.actor,
                organization_id=workspace.organization_id,
                workspace_id=command.workspace_id,
                prepared=prepared,
                now=now,
            )
            context = _PublicationContext(
                actor=command.actor,
                organization_id=workspace.organization_id,
                workspace_id=command.workspace_id,
                skill_id=command.resource_scope_id,
                now=now,
            )
            selected, outcome, created = await _select_revision(
                session,
                context=context,
                locked=locked,
                default=default,
                prepared=prepared,
            )
            if upload is not None:
                upload.consumed_by_revision_id = selected.id
            result = SkillPublicationReceipt(
                skill=locked.to_resource(),
                revision=selected.to_resource(),
                outcome=outcome,
            )
            session.add(
                skill_audit_record(
                    actor=command.actor,
                    organization_id=workspace.organization_id,
                    workspace_id=command.workspace_id,
                    skill_id=command.resource_scope_id,
                    action="skill.revision.publish",
                    now=now,
                    details={
                        "previous_revision_id": default.id,
                        "selected_revision_id": selected.id,
                        "source_kind": request.source.kind,
                        "publication_outcome": outcome,
                    },
                )
            )
            session.add(
                new_replay_evidence(
                    scope=scope,
                    response=result,
                    now=now,
                )
            )
            await session.flush()
            return ReplayResult(result=result, created=created)

    async def _preauthorize_replay(
        self, command: _ReplayCommand
    ) -> tuple[ReplayResult[SkillPublicationReceipt] | None, str]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await authorize_skill_workspace(
                session,
                actor=command.actor,
                workspace_id=command.workspace_id,
                action=command.action,
                concealed_code=(
                    "skill_not_found" if command.resource_scope_id != command.workspace_id else "resource_not_found"
                ),
            )
            replay = await load_replay(
                session,
                scope=command.scope(workspace.organization_id),
                now=now,
                response_model=SkillPublicationReceipt,
            )
            return replay, workspace.organization_id

    async def _require_replay(self, command: _ReplayCommand) -> ReplayResult[SkillPublicationReceipt]:
        replay, _organization_id = await self._preauthorize_replay(command)
        if replay is None:
            raise SkillError(
                "idempotency_conflict",
                "The idempotent Skill publication could not be reconciled.",
                category=ErrorCategory.conflict,
            )
        return replay

    async def _lock_upload(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        prepared: PreparedSkillSource,
        now: datetime,
    ) -> SkillUploadRecord | None:
        await require_object_publications(
            session,
            (skill_package_object_key(organization_id, workspace_id, prepared.package.manifest.content_digest),),
        )
        if prepared.upload_id is None:
            return None
        upload = await require_owned_upload(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            upload_id=prepared.upload_id,
            now=now,
            for_update=True,
        )
        if require_upload_manifest(upload) != prepared.package.manifest:
            raise SkillError(
                "skill_package_invalid",
                "The staged Skill package does not match its receipt.",
                category=ErrorCategory.invalid_request,
            )
        return upload

    async def _audit_failure(
        self,
        error: Exception,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        skill_id: str | None,
        action: str,
    ) -> None:
        try:
            await record_failed_skill_attempt(
                self._sessions,
                actor=actor,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action=action,
                now=self._clock(),
            )
        except Exception as audit_error:
            error.add_note(f"security audit persistence failed with {type(audit_error).__name__}")


async def _select_revision(
    session: AsyncSession,
    *,
    context: _PublicationContext,
    locked: SkillRecord,
    default: SkillRevisionRecord,
    prepared: PreparedSkillSource,
) -> tuple[SkillRevisionRecord, Literal["published", "already_default"], bool]:
    """Append ``head.version + 1`` unless the content already equals the default Revision."""

    if default.content_digest == prepared.package.manifest.content_digest:
        return default, "already_default", False
    selected = _new_revision_record(
        context,
        revision_id=new_skill_revision_id(),
        version=locked.version + 1,
        prepared=prepared,
    )
    session.add(selected)
    await session.flush((selected,))
    locked.default_revision_id = selected.id
    locked.version = selected.version
    locked.updated_by_type = context.actor.principal.principal_type.value
    locked.updated_by_id = context.actor.principal.principal_id
    locked.updated_at = context.now
    return selected, "published", True


def _new_skill_record(context: _PublicationContext, *, key: str, name: str, revision_id: str) -> SkillRecord:
    return SkillRecord(
        id=context.skill_id,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        key=key,
        name=name,
        labels=context.labels,
        version=1,
        default_revision_id=revision_id,
        created_by_type=context.actor.principal.principal_type.value,
        created_by_id=context.actor.principal.principal_id,
        updated_by_type=context.actor.principal.principal_type.value,
        updated_by_id=context.actor.principal.principal_id,
        created_at=context.now,
        updated_at=context.now,
        deleted_at=None,
    )


def _new_revision_record(
    context: _PublicationContext,
    *,
    revision_id: str,
    version: int,
    prepared: PreparedSkillSource,
) -> SkillRevisionRecord:
    return SkillRevisionRecord(
        id=revision_id,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        skill_id=context.skill_id,
        version=version,
        content_digest=prepared.package.manifest.content_digest,
        manifest=prepared.package.manifest.model_dump(mode="json"),
        imported_from=prepared.provenance.model_dump(mode="json"),
        created_by_type=context.actor.principal.principal_type.value,
        created_by_id=context.actor.principal.principal_id,
        created_at=context.now,
    )
