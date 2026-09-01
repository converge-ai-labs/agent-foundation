"""Transactional AgentPreset application service."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_agent_preset,
    authorize_agent_preset_collection,
    authorize_workspace,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction

from .cursors import (
    AgentPresetCursorError,
    decode_preset_cursor,
    decode_revision_cursor,
    encode_preset_cursor,
    encode_revision_cursor,
)
from .domain import (
    AgentPreset,
    AgentPresetCollection,
    AgentPresetCommandRequest,
    AgentPresetConfig,
    AgentPresetLifecycleState,
    AgentPresetPublishResult,
    AgentPresetRevision,
    AgentPresetRevisionCollection,
    AgentPresetSource,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    PatchAgentPresetRequest,
    PluginRuntimeMode,
    ReplaceAgentPresetConfigRequest,
    ResolvedRevisionContent,
    RollbackAgentPresetRequest,
    canonical_digest,
    new_agent_preset_id,
    new_agent_preset_revision_id,
)
from .errors import (
    AgentPresetError,
    preset_not_found,
    preset_revision_not_found,
    resource_version_conflict,
)
from .invocation_resolution import AgentPresetInvocationResolver, PreparedAgentPresetEnable
from .models import AgentPresetRecord, AgentPresetRevisionRecord
from .resolution import AgentPresetResolver, PreparedRevisionResolution, resolution_error

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512
_CONFIG_ADAPTER = TypeAdapter(AgentPresetConfig)


class AgentPresetService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentPresetResolver,
        invocation_resolver: AgentPresetInvocationResolver,
        *,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._invocation_resolver = invocation_resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateAgentPresetRequest,
    ) -> AgentPreset:
        identity = _identity(idempotency_key, request)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_preset_create,
                )
                replay = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent_preset.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay is not None:
                    return await _load_preset_resource(
                        session,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        preset_id=replay,
                    )
                preset_id = new_agent_preset_id()
                config_payload = request.config.model_dump(mode="json", by_alias=True)
                record = AgentPresetRecord(
                    id=preset_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    source=AgentPresetSource.custom.value,
                    name=request.name,
                    normalized_name=_normalized_name(request.name),
                    description=request.description,
                    lifecycle_state=AgentPresetLifecycleState.enabled.value,
                    resource_version=1,
                    config=config_payload,
                    config_digest=canonical_digest(request.config),
                    active_revision_id=None,
                    config_base_revision_id=None,
                    config_base_digest=None,
                    duplicated_from_preset_id=None,
                    duplicated_from_revision_id=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        operation="agent_preset.create",
                        scope_id=workspace_id,
                        identity=identity,
                        result_kind="agent_preset",
                        result_ref=preset_id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        action="agent_preset.create",
                        preset_id=preset_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                return await self._replay_preset(
                    actor=actor,
                    workspace_id=workspace_id,
                    operation="agent_preset.create",
                    scope_id=workspace_id,
                    identity=identity,
                )
            raise AgentPresetError(
                "preset_name_conflict",
                "An AgentPreset with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def get(self, *, actor: AuthenticatedActor, preset_id: str) -> AgentPreset:
        async with transaction(self._sessions) as session:
            workspace = await _authorize_preset(
                session,
                actor=actor,
                preset_id=preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            return await _load_preset_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                preset_id=preset_id,
            )

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        lifecycle_state: AgentPresetLifecycleState | None,
        source: AgentPresetSource | None,
        include_archived: bool,
    ) -> AgentPresetCollection:
        scope = {
            "workspace_id": workspace_id,
            "lifecycle_state": lifecycle_state.value if lifecycle_state is not None else None,
            "source": source.value if source is not None else None,
            "include_archived": include_archived,
        }
        try:
            after = decode_preset_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentPresetCursorError as error:
            raise AgentPresetError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        try:
            async with transaction(self._sessions) as session:
                authorization = await authorize_agent_preset_collection(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                )
                workspace = authorization.workspace
                query = select(AgentPresetRecord).where(
                    AgentPresetRecord.organization_id == workspace.organization_id,
                    AgentPresetRecord.workspace_id == workspace_id,
                )
                if authorization.visible_preset_ids is not None:
                    query = query.where(AgentPresetRecord.id.in_(authorization.visible_preset_ids))
                if lifecycle_state is not None:
                    query = query.where(AgentPresetRecord.lifecycle_state == lifecycle_state.value)
                elif not include_archived:
                    query = query.where(AgentPresetRecord.lifecycle_state != AgentPresetLifecycleState.archived.value)
                if source is not None:
                    query = query.where(AgentPresetRecord.source == source.value)
                if after is not None:
                    updated_at, preset_id = after
                    query = query.where(
                        or_(
                            AgentPresetRecord.updated_at < updated_at,
                            and_(AgentPresetRecord.updated_at == updated_at, AgentPresetRecord.id < preset_id),
                        )
                    )
                rows = tuple(
                    (
                        await session.scalars(
                            query.order_by(AgentPresetRecord.updated_at.desc(), AgentPresetRecord.id.desc()).limit(
                                limit + 1
                            )
                        )
                    ).all()
                )
                page = rows[:limit]
                next_cursor = None
                if len(rows) > limit and page:
                    next_cursor = encode_preset_cursor(
                        updated_at=page[-1].updated_at,
                        preset_id=page[-1].id,
                        scope=scope,
                    )
                return AgentPresetCollection(
                    items=tuple(record.to_resource() for record in page),
                    next_cursor=next_cursor,
                )
        except AuthorizationError as error:
            raise _authorization_error(error) from error

    async def patch_metadata(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        request: PatchAgentPresetRequest,
    ) -> AgentPreset:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_update,
                )
                record = await _locked_preset(session, workspace.organization_id, workspace.workspace_id, preset_id)
                _require_custom_mutable(record)
                _require_version(record, request.expected_resource_version)
                if "name" in request.model_fields_set:
                    assert request.name is not None
                    record.name = request.name
                    record.normalized_name = _normalized_name(request.name)
                if "description" in request.model_fields_set:
                    record.description = request.description
                _touch(record, actor=actor, now=now)
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="agent_preset.update",
                        preset_id=preset_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            raise AgentPresetError(
                "preset_name_conflict",
                "An AgentPreset with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def replace_config(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        request: ReplaceAgentPresetConfigRequest,
    ) -> AgentPreset:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_update,
                )
                record = await _locked_preset(session, workspace.organization_id, workspace.workspace_id, preset_id)
                _require_custom_mutable(record)
                _require_version(record, request.expected_resource_version)
                record.config = request.config.model_dump(mode="json", by_alias=True)
                record.config_digest = canonical_digest(request.config)
                _touch(record, actor=actor, now=now)
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="agent_preset.update",
                        preset_id=preset_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error

    async def publish(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        idempotency_key: str,
        request: AgentPresetCommandRequest,
    ) -> AgentPresetPublishResult:
        identity = _identity(idempotency_key, request)
        replay = await self._publish_replay(
            actor=actor,
            preset_id=preset_id,
            operation="agent_preset.publish",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self.get(actor=actor, preset_id=preset_id)
        _require_custom_resource(current)
        if current.lifecycle_state is AgentPresetLifecycleState.archived:
            raise _archived()
        if current.resource_version != request.expected_resource_version:
            raise resource_version_conflict(current.resource_version)
        prepared = await self._prepare_resolution(actor=actor, preset=current)
        return await self._commit_publish(
            actor=actor,
            preset_id=preset_id,
            expected_version=request.expected_resource_version,
            operation="agent_preset.publish",
            identity=identity,
            prepared=prepared,
            source_revision_id=None,
        )

    async def rollback(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        idempotency_key: str,
        request: RollbackAgentPresetRequest,
    ) -> AgentPresetPublishResult:
        identity = _identity(idempotency_key, request)
        replay = await self._publish_replay(
            actor=actor,
            preset_id=preset_id,
            operation="agent_preset.rollback",
            identity=identity,
        )
        if replay is not None:
            return replay
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_publish,
                )
                record = await _locked_preset(session, workspace.organization_id, workspace.workspace_id, preset_id)
                _require_custom_mutable(record)
                _require_version(record, request.expected_resource_version)
                if record.config_digest != record.config_base_digest:
                    raise AgentPresetError(
                        "config_has_unpublished_changes",
                        "Rollback would discard unpublished AgentPreset configuration changes.",
                        status_code=409,
                    )
                source = await _locked_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    preset_id=preset_id,
                    revision_id=request.source_revision_id,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent_preset.rollback",
                    scope_id=preset_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _publish_result_from_revision(session, replay_ref)
                revision = _copy_revision(
                    source,
                    revision_id=new_agent_preset_revision_id(),
                    revision_number=await _next_revision_number(session, preset_id),
                    source_revision_id=source.id,
                    actor=actor,
                    now=now,
                )
                session.add(revision)
                record.config = source.config
                record.config_digest = source.config_digest
                record.active_revision_id = revision.id
                record.config_base_revision_id = revision.id
                record.config_base_digest = source.config_digest
                _touch(record, actor=actor, now=now)
                _add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation="agent_preset.rollback",
                    identity=identity,
                    result_ref=revision.id,
                    now=now,
                )
                await session.flush()
                return AgentPresetPublishResult(preset=record.to_resource(), revision=revision.to_resource())
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._publish_replay(
                    actor=actor,
                    preset_id=preset_id,
                    operation="agent_preset.rollback",
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

    async def duplicate(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        idempotency_key: str,
        request: DuplicateAgentPresetRequest,
    ) -> AgentPreset:
        identity = _identity(idempotency_key, request)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                source_workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_read,
                )
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=source_workspace.workspace_id,
                    action=WorkspaceAction.agent_preset_duplicate,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent_preset.duplicate",
                    scope_id=preset_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _load_preset_resource(
                        session,
                        organization_id=source_workspace.organization_id,
                        workspace_id=source_workspace.workspace_id,
                        preset_id=replay_ref,
                    )
                source = await _locked_preset(
                    session,
                    source_workspace.organization_id,
                    source_workspace.workspace_id,
                    preset_id,
                )
                _require_version(source, request.expected_resource_version)
                if source.lifecycle_state == AgentPresetLifecycleState.archived.value:
                    raise _archived()
                if source.active_revision_id is None:
                    raise AgentPresetError(
                        "preset_not_published", "The AgentPreset has no active Revision.", status_code=409
                    )
                source_revision = await _locked_revision(
                    session,
                    organization_id=source_workspace.organization_id,
                    workspace_id=source_workspace.workspace_id,
                    preset_id=preset_id,
                    revision_id=source.active_revision_id,
                )
                new_preset_id = new_agent_preset_id()
                new_revision_id = new_agent_preset_revision_id()
                duplicate = AgentPresetRecord(
                    id=new_preset_id,
                    organization_id=source.organization_id,
                    workspace_id=source.workspace_id,
                    source=AgentPresetSource.custom.value,
                    name=request.name,
                    normalized_name=_normalized_name(request.name),
                    description=request.description,
                    lifecycle_state=AgentPresetLifecycleState.enabled.value,
                    resource_version=1,
                    config=source_revision.config,
                    config_digest=source_revision.config_digest,
                    active_revision_id=new_revision_id,
                    config_base_revision_id=new_revision_id,
                    config_base_digest=source_revision.config_digest,
                    duplicated_from_preset_id=source.id,
                    duplicated_from_revision_id=source_revision.id,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                revision = _copy_revision(
                    source_revision,
                    revision_id=new_revision_id,
                    revision_number=1,
                    source_revision_id=source_revision.id,
                    agent_preset_id=new_preset_id,
                    actor=actor,
                    now=now,
                )
                session.add_all((duplicate, revision))
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        operation="agent_preset.duplicate",
                        scope_id=preset_id,
                        identity=identity,
                        result_kind="agent_preset",
                        result_ref=duplicate.id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        action="agent_preset.duplicate",
                        preset_id=new_preset_id,
                        now=now,
                    )
                )
                await session.flush()
                return duplicate.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._duplicate_replay(actor=actor, preset_id=preset_id, identity=identity)
                if replay is not None:
                    return replay
            raise AgentPresetError(
                "preset_name_conflict",
                "An AgentPreset with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def _duplicate_replay(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        identity: tuple[str, str],
    ) -> AgentPreset | None:
        async with transaction(self._sessions) as session:
            source_workspace = await _authorize_preset(
                session,
                actor=actor,
                preset_id=preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=source_workspace.workspace_id,
                action=WorkspaceAction.agent_preset_duplicate,
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation="agent_preset.duplicate",
                scope_id=preset_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return await _load_preset_resource(
                session,
                organization_id=source_workspace.organization_id,
                workspace_id=source_workspace.workspace_id,
                preset_id=replay_ref,
            )

    async def change_lifecycle(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        action: Literal["enable", "disable", "archive", "unarchive"],
        idempotency_key: str,
        request: AgentPresetCommandRequest,
    ) -> AgentPreset:
        operation = f"agent_preset.{action}"
        identity = _identity(idempotency_key, request)
        replay = await self._preset_command_replay(
            actor=actor,
            preset_id=preset_id,
            operation=operation,
            identity=identity,
        )
        if replay is not None:
            return replay
        prepared_enable: PreparedAgentPresetEnable | None = None
        if action == "enable":
            current = await self.get(actor=actor, preset_id=preset_id)
            if current.resource_version != request.expected_resource_version:
                raise resource_version_conflict(current.resource_version)
            if current.lifecycle_state is not AgentPresetLifecycleState.disabled or current.active_revision_id is None:
                raise AgentPresetError(
                    "preset_state_conflict",
                    "The AgentPreset cannot be enabled from its current state.",
                    status_code=409,
                )
            prepared_enable = await self._invocation_resolver.prepare_enable_revalidation(
                actor=actor,
                agent_preset_id=preset_id,
            )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_lifecycle,
                )
                record = await _locked_preset(session, workspace.organization_id, workspace.workspace_id, preset_id)
                _require_version(record, request.expected_resource_version)
                _apply_lifecycle_transition(session, record, action=action)
                if prepared_enable is not None:
                    await self._invocation_resolver.freeze_enable_revalidation(
                        session,
                        prepared=prepared_enable,
                    )
                if action == "disable":
                    await _require_not_in_use(session, record)
                _touch(record, actor=actor, now=now)
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        operation=operation,
                        scope_id=preset_id,
                        identity=identity,
                        result_kind="agent_preset",
                        result_ref=preset_id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action=operation,
                        preset_id=preset_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._preset_command_replay(
                    actor=actor,
                    preset_id=preset_id,
                    operation=operation,
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        limit: int,
        cursor: str | None,
    ) -> AgentPresetRevisionCollection:
        scope: dict[str, object] = {"preset_id": preset_id}
        try:
            after = decode_revision_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentPresetCursorError as error:
            raise AgentPresetError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize_preset(
                session,
                actor=actor,
                preset_id=preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            query = select(AgentPresetRevisionRecord).where(
                AgentPresetRevisionRecord.organization_id == workspace.organization_id,
                AgentPresetRevisionRecord.workspace_id == workspace.workspace_id,
                AgentPresetRevisionRecord.agent_preset_id == preset_id,
            )
            if after is not None:
                number, revision_id = after
                query = query.where(
                    or_(
                        AgentPresetRevisionRecord.revision_number < number,
                        and_(
                            AgentPresetRevisionRecord.revision_number == number,
                            AgentPresetRevisionRecord.id < revision_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            AgentPresetRevisionRecord.revision_number.desc(),
                            AgentPresetRevisionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit and page:
                next_cursor = encode_revision_cursor(
                    revision_number=page[-1].revision_number,
                    revision_id=page[-1].id,
                    scope=scope,
                )
            return AgentPresetRevisionCollection(
                items=tuple(item.to_resource() for item in page), next_cursor=next_cursor
            )

    async def get_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
        preset_id: str | None = None,
    ) -> AgentPresetRevision:
        async with transaction(self._sessions) as session:
            revision = await session.scalar(
                select(AgentPresetRevisionRecord).where(AgentPresetRevisionRecord.id == revision_id)
            )
            if revision is None or (preset_id is not None and revision.agent_preset_id != preset_id):
                raise preset_revision_not_found()
            await _authorize_preset(
                session,
                actor=actor,
                preset_id=revision.agent_preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            return revision.to_resource()

    async def _prepare_resolution(
        self, *, actor: AuthenticatedActor, preset: AgentPreset
    ) -> PreparedRevisionResolution:
        try:
            return await self._resolver.prepare(
                actor=actor,
                organization_id=preset.organization_id,
                workspace_id=preset.workspace_id,
                agent_preset_id=preset.id,
                config=preset.config,
            )
        except Exception as error:
            raise resolution_error(error) from error

    async def _commit_publish(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        expected_version: int,
        operation: str,
        identity: tuple[str, str],
        prepared: PreparedRevisionResolution,
        source_revision_id: str | None,
    ) -> AgentPresetPublishResult:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_preset(
                    session,
                    actor=actor,
                    preset_id=preset_id,
                    action=WorkspaceAction.agent_preset_publish,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation=operation,
                    scope_id=preset_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _publish_result_from_revision(session, replay_ref)
                record = await _locked_preset(session, workspace.organization_id, workspace.workspace_id, preset_id)
                _require_custom_mutable(record)
                _require_version(record, expected_version)
                if record.config_digest != canonical_digest(prepared.config):
                    raise resource_version_conflict(record.resource_version)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                revision = _new_revision(
                    record,
                    revision_id=new_agent_preset_revision_id(),
                    revision_number=await _next_revision_number(session, preset_id),
                    mode=self._resolver.plugin_runtime_mode,
                    config=prepared.config,
                    resolved=resolved,
                    source_revision_id=source_revision_id,
                    actor=actor,
                    now=now,
                )
                session.add(revision)
                record.active_revision_id = revision.id
                record.config_base_revision_id = revision.id
                record.config_base_digest = record.config_digest
                _touch(record, actor=actor, now=now)
                _add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation=operation,
                    identity=identity,
                    result_ref=revision.id,
                    now=now,
                )
                await session.flush()
                return AgentPresetPublishResult(preset=record.to_resource(), revision=revision.to_resource())
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._publish_replay(
                    actor=actor,
                    preset_id=preset_id,
                    operation=operation,
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

    async def _publish_replay(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        operation: str,
        identity: tuple[str, str],
    ) -> AgentPresetPublishResult | None:
        async with transaction(self._sessions) as session:
            await _authorize_preset(
                session,
                actor=actor,
                preset_id=preset_id,
                action=(
                    WorkspaceAction.agent_preset_duplicate
                    if operation == "agent_preset.duplicate"
                    else WorkspaceAction.agent_preset_publish
                ),
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=preset_id,
                identity=identity,
                now=self._clock(),
            )
            return None if replay_ref is None else await _publish_result_from_revision(session, replay_ref)

    async def _preset_command_replay(
        self,
        *,
        actor: AuthenticatedActor,
        preset_id: str,
        operation: str,
        identity: tuple[str, str],
    ) -> AgentPreset | None:
        async with transaction(self._sessions) as session:
            workspace = await _authorize_preset(
                session,
                actor=actor,
                preset_id=preset_id,
                action=WorkspaceAction.agent_preset_lifecycle,
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=preset_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return await _load_preset_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                preset_id=replay_ref,
            )

    async def _replay_preset(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        operation: str,
        scope_id: str,
        identity: tuple[str, str],
    ) -> AgentPreset:
        async with transaction(self._sessions) as session:
            workspace = await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.agent_preset_create,
            )
            result_ref = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=scope_id,
                identity=identity,
                now=self._clock(),
            )
            if result_ref is None:
                raise _idempotency_conflict()
            return await _load_preset_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                preset_id=result_ref,
            )


async def _authorize_preset(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    preset_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_agent_preset(
            session,
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            agent_preset_id=preset_id,
            action=action,
        )
    except AuthorizationError as error:
        raise _authorization_error(error, exact=True) from error


async def _load_preset_resource(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    preset_id: str,
) -> AgentPreset:
    record = await session.scalar(
        select(AgentPresetRecord).where(
            AgentPresetRecord.id == preset_id,
            AgentPresetRecord.organization_id == organization_id,
            AgentPresetRecord.workspace_id == workspace_id,
        )
    )
    if record is None:
        raise preset_not_found()
    return record.to_resource()


async def _locked_preset(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    preset_id: str,
) -> AgentPresetRecord:
    record = await session.scalar(
        select(AgentPresetRecord)
        .where(
            AgentPresetRecord.id == preset_id,
            AgentPresetRecord.organization_id == organization_id,
            AgentPresetRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None:
        raise preset_not_found()
    return record


async def _locked_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    preset_id: str,
    revision_id: str,
) -> AgentPresetRevisionRecord:
    record = await session.scalar(
        select(AgentPresetRevisionRecord)
        .where(
            AgentPresetRevisionRecord.id == revision_id,
            AgentPresetRevisionRecord.agent_preset_id == preset_id,
            AgentPresetRevisionRecord.organization_id == organization_id,
            AgentPresetRevisionRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None:
        raise preset_revision_not_found()
    return record


async def _next_revision_number(session: AsyncSession, preset_id: str) -> int:
    latest = await session.scalar(
        select(AgentPresetRevisionRecord.revision_number)
        .where(AgentPresetRevisionRecord.agent_preset_id == preset_id)
        .order_by(AgentPresetRevisionRecord.revision_number.desc())
        .limit(1)
    )
    return 1 if latest is None else latest + 1


def _new_revision(
    preset: AgentPresetRecord,
    *,
    revision_id: str,
    revision_number: int,
    mode: PluginRuntimeMode,
    config: AgentPresetConfig,
    resolved: ResolvedRevisionContent,
    source_revision_id: str | None,
    actor: AuthenticatedActor,
    now: datetime,
) -> AgentPresetRevisionRecord:
    config_payload = config.model_dump(mode="json", by_alias=True)
    resolved_payload = resolved.model_dump(mode="json", by_alias=True)
    content_digest = canonical_digest(
        {
            "plugin_runtime_mode": mode.value,
            "config": config_payload,
            "resolved": resolved_payload,
        }
    )
    return AgentPresetRevisionRecord(
        id=revision_id,
        organization_id=preset.organization_id,
        workspace_id=preset.workspace_id,
        agent_preset_id=preset.id,
        revision_number=revision_number,
        plugin_runtime_mode=mode.value,
        config=config_payload,
        config_digest=canonical_digest(config),
        resolved_model=resolved.resolved_model.model_dump(mode="json"),
        resolved_plugin_versions=[item.model_dump(mode="json") for item in resolved.resolved_plugin_versions],
        runtime_lock_digest=resolved.runtime_lock_digest,
        resolved_skills=[item.model_dump(mode="json") for item in resolved.resolved_skills],
        resolved_connectors=[item.model_dump(mode="json") for item in resolved.resolved_connectors],
        resolved_environment=(
            resolved.resolved_environment.model_dump(mode="json") if resolved.resolved_environment is not None else None
        ),
        resolved_subagents=[item.model_dump(mode="json") for item in resolved.resolved_subagents],
        content_digest=content_digest,
        source_revision_id=source_revision_id,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


def _copy_revision(
    source: AgentPresetRevisionRecord,
    *,
    revision_id: str,
    revision_number: int,
    source_revision_id: str,
    actor: AuthenticatedActor,
    now: datetime,
    agent_preset_id: str | None = None,
) -> AgentPresetRevisionRecord:
    return AgentPresetRevisionRecord(
        id=revision_id,
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        agent_preset_id=agent_preset_id or source.agent_preset_id,
        revision_number=revision_number,
        plugin_runtime_mode=source.plugin_runtime_mode,
        config=source.config,
        config_digest=source.config_digest,
        resolved_model=source.resolved_model,
        resolved_plugin_versions=source.resolved_plugin_versions,
        runtime_lock_digest=source.runtime_lock_digest,
        resolved_skills=source.resolved_skills,
        resolved_connectors=source.resolved_connectors,
        resolved_environment=source.resolved_environment,
        resolved_subagents=source.resolved_subagents,
        content_digest=source.content_digest,
        source_revision_id=source_revision_id,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


async def _publish_result_from_revision(session: AsyncSession, revision_id: str) -> AgentPresetPublishResult:
    revision = await session.scalar(
        select(AgentPresetRevisionRecord).where(AgentPresetRevisionRecord.id == revision_id)
    )
    if revision is None:
        raise _idempotency_conflict()
    preset = await _load_preset_resource(
        session,
        organization_id=revision.organization_id,
        workspace_id=revision.workspace_id,
        preset_id=revision.agent_preset_id,
    )
    return AgentPresetPublishResult(preset=preset, revision=revision.to_resource())


def _require_custom_resource(preset: AgentPreset) -> None:
    if preset.source is AgentPresetSource.builtin:
        raise AgentPresetError("preset_state_conflict", "Built-in AgentPresets are read-only.", status_code=409)


def _require_custom_mutable(record: AgentPresetRecord) -> None:
    if record.source != AgentPresetSource.custom.value:
        raise AgentPresetError("preset_state_conflict", "Built-in AgentPresets are read-only.", status_code=409)
    if record.lifecycle_state == AgentPresetLifecycleState.archived.value:
        raise _archived()


def _require_version(record: AgentPresetRecord, expected: int) -> None:
    if record.resource_version != expected:
        raise resource_version_conflict(record.resource_version)


def _touch(record: AgentPresetRecord, *, actor: AuthenticatedActor, now: datetime) -> None:
    record.resource_version += 1
    record.updated_by_type = actor.principal.principal_type.value
    record.updated_by_id = actor.principal.principal_id
    record.updated_at = now


def _apply_lifecycle_transition(
    _session: AsyncSession,
    record: AgentPresetRecord,
    *,
    action: Literal["enable", "disable", "archive", "unarchive"],
) -> None:
    current = AgentPresetLifecycleState(record.lifecycle_state)
    if action == "enable":
        if current is not AgentPresetLifecycleState.disabled or record.active_revision_id is None:
            raise AgentPresetError(
                "preset_state_conflict", "The AgentPreset cannot be enabled from its current state.", status_code=409
            )
        record.lifecycle_state = AgentPresetLifecycleState.enabled.value
        return
    if action == "disable":
        if current is not AgentPresetLifecycleState.enabled:
            raise AgentPresetError(
                "preset_state_conflict", "The AgentPreset cannot be disabled from its current state.", status_code=409
            )
        record.lifecycle_state = AgentPresetLifecycleState.disabled.value
        return
    if action == "archive":
        if record.source != AgentPresetSource.custom.value or current is not AgentPresetLifecycleState.disabled:
            raise AgentPresetError(
                "preset_state_conflict", "Only a disabled custom AgentPreset can be archived.", status_code=409
            )
        record.lifecycle_state = AgentPresetLifecycleState.archived.value
        return
    if current is not AgentPresetLifecycleState.archived or record.source != AgentPresetSource.custom.value:
        raise AgentPresetError(
            "preset_state_conflict", "The AgentPreset cannot be unarchived from its current state.", status_code=409
        )
    record.lifecycle_state = AgentPresetLifecycleState.disabled.value


async def _require_not_in_use(session: AsyncSession, target: AgentPresetRecord) -> None:
    active_revisions = tuple(
        (
            await session.scalars(
                select(AgentPresetRevisionRecord)
                .join(AgentPresetRecord, AgentPresetRecord.active_revision_id == AgentPresetRevisionRecord.id)
                .where(
                    AgentPresetRecord.organization_id == target.organization_id,
                    AgentPresetRecord.workspace_id == target.workspace_id,
                    AgentPresetRecord.lifecycle_state == AgentPresetLifecycleState.enabled.value,
                    AgentPresetRecord.id != target.id,
                )
            )
        ).all()
    )
    pending = list(active_revisions)
    visited: set[str] = set()
    while pending:
        revision = pending.pop()
        if revision.id in visited:
            continue
        visited.add(revision.id)
        child_revision_ids: list[str] = []
        for edge in revision.resolved_subagents:
            if edge.get("child_agent_preset_id") == target.id:
                raise AgentPresetError(
                    "preset_in_use",
                    "The AgentPreset is referenced by an enabled AgentPreset graph.",
                    status_code=409,
                )
            child_revision_id = edge.get("child_agent_preset_revision_id")
            if isinstance(child_revision_id, str) and child_revision_id not in visited:
                child_revision_ids.append(child_revision_id)
        if child_revision_ids:
            pending.extend(
                (
                    await session.scalars(
                        select(AgentPresetRevisionRecord).where(
                            AgentPresetRevisionRecord.organization_id == target.organization_id,
                            AgentPresetRevisionRecord.workspace_id == target.workspace_id,
                            AgentPresetRevisionRecord.id.in_(child_revision_ids),
                        )
                    )
                ).all()
            )


def _add_command_evidence_and_audit(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: AgentPresetRecord,
    operation: str,
    identity: tuple[str, str],
    result_ref: str,
    now: datetime,
) -> None:
    session.add(
        _evidence(
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            operation=operation,
            scope_id=record.id,
            identity=identity,
            result_kind="agent_preset_revision",
            result_ref=result_ref,
            now=now,
        )
    )
    session.add(
        _audit(
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            action=operation,
            preset_id=record.id,
            now=now,
        )
    )


def _identity(idempotency_key: str, request) -> tuple[str, str]:
    try:
        encoded = idempotency_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if not 1 <= len(encoded) <= _MAX_IDEMPOTENCY_KEY_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise _invalid_idempotency_key()
    request_digest = canonical_digest(request)
    return hashlib.sha256(encoded).hexdigest(), request_digest


async def _load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    now: datetime,
) -> str | None:
    key_digest, request_digest = identity
    evidence = await session.scalar(
        select(IdempotencyEvidenceRecord).where(
            IdempotencyEvidenceRecord.workspace_id == actor.boundary_workspace_id,
            IdempotencyEvidenceRecord.actor_type == actor.principal.principal_type.value,
            IdempotencyEvidenceRecord.actor_id == actor.principal.principal_id,
            IdempotencyEvidenceRecord.operation == operation,
            IdempotencyEvidenceRecord.scope_id == scope_id,
            IdempotencyEvidenceRecord.key_digest == key_digest,
            IdempotencyEvidenceRecord.expires_at > now,
        )
    )
    if evidence is None:
        return None
    if evidence.request_digest != request_digest:
        raise _idempotency_conflict()
    return evidence.result_ref


def _evidence(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    key_digest, request_digest = identity
    return IdempotencyEvidenceRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        key_digest=key_digest,
        request_digest=request_digest,
        result_kind=result_kind,
        result_ref=result_ref,
        created_at=now,
        expires_at=now + IDEMPOTENCY_LIFETIME,
    )


def _audit(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    preset_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("audit"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="agent_preset",
        resource_id=preset_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def _normalized_name(value: str) -> str:
    return value.casefold()


def _authorization_error(error: AuthorizationError, *, exact: bool = False) -> AgentPresetError:
    if exact or error.concealed:
        return preset_not_found()
    return AgentPresetError("forbidden", "The operation is not allowed.", status_code=403)


def _archived() -> AgentPresetError:
    return AgentPresetError("preset_archived", "The AgentPreset is archived.", status_code=409)


def _invalid_idempotency_key() -> AgentPresetError:
    return AgentPresetError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def _idempotency_conflict() -> AgentPresetError:
    return AgentPresetError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        status_code=409,
    )


def _is_idempotency_race(error: IntegrityError) -> bool:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == "uq_idempotency_evidence_replay_scope"
    message = str(error).lower()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message
