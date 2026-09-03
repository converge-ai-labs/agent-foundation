"""Transactional Agent application service."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_agent,
    authorize_agent_collection,
    authorize_workspace,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .cursors import (
    AgentCursorError,
    decode_agent_cursor,
    decode_revision_cursor,
    encode_agent_cursor,
    encode_revision_cursor,
)
from .domain import (
    Agent,
    AgentCollection,
    AgentConfig,
    AgentRevision,
    AgentRevisionCollection,
    AgentRevisionCreateResult,
    AgentSource,
    BuiltinAgentRegistration,
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    JsonObject,
    PluginRuntimeMode,
    ResolvedRevisionContent,
    RestoreAgentRevisionRequest,
    UpdateAgentRequest,
    canonical_digest,
    new_agent_id,
    new_agent_revision_id,
)
from .errors import (
    AgentError,
    agent_not_found,
    agent_revision_not_found,
    agent_version_conflict,
)
from .invocation_resolution import AgentInvocationResolver, PreparedAgentRevisionGraph, RootAgentStatePolicy
from .models import AgentRecord, AgentRevisionRecord
from .resolution import AgentResolver, PreparedRevisionResolution, resolution_error

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512
_CONFIG_ADAPTER = TypeAdapter(AgentConfig)


class AgentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        invocation_resolver: AgentInvocationResolver,
        *,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._invocation_resolver = invocation_resolver
        self._clock = clock or utc_now

    async def register_builtin(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        registration: BuiltinAgentRegistration,
    ) -> AgentRevisionCreateResult:
        """Register or upgrade one distribution-owned built-in Agent."""

        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                current = await session.get(AgentRecord, registration.agent_id)
                if current is not None and (
                    current.organization_id != workspace.organization_id
                    or current.workspace_id != workspace.workspace_id
                    or current.source != AgentSource.builtin.value
                ):
                    raise _builtin_identity_conflict()
                organization_id = workspace.organization_id
        except AuthorizationError as error:
            raise _authorization_error(error) from error

        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=registration.agent_id,
                config=registration.config,
            )
        except Exception as error:
            raise resolution_error(error) from error

        expected_content_digest: str | None = None
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                record = await session.scalar(
                    select(AgentRecord).where(AgentRecord.id == registration.agent_id).with_for_update()
                )
                created = record is None
                revision_id = new_agent_revision_id()
                if created:
                    record = _new_builtin_agent(
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        revision_id=revision_id,
                        registration=registration,
                        now=now,
                    )
                elif (
                    record.organization_id != workspace.organization_id
                    or record.workspace_id != workspace.workspace_id
                    or record.source != AgentSource.builtin.value
                ):
                    raise _builtin_identity_conflict()
                elif not record.enabled or record.archived_at is not None:
                    raise AgentError(
                        "agent_state_conflict",
                        "The built-in Agent is not available.",
                        status_code=409,
                    )

                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                revision = _new_revision(
                    record,
                    revision_id=revision_id,
                    version=1 if created else record.version + 1,
                    mode=self._resolver.plugin_runtime_mode,
                    config=registration.config,
                    resolved=resolved,
                    source_revision_id=None,
                    actor=actor,
                    now=now,
                )
                revision.created_by_type = "system"
                revision.created_by_id = registration.system_actor_id
                expected_content_digest = revision.content_digest
                current_revision = (
                    None
                    if created
                    else await _locked_revision(
                        session,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        agent_id=record.id,
                        revision_id=record.current_revision_id,
                    )
                )
                content_changed = current_revision is None or current_revision.content_digest != revision.content_digest
                metadata_changed = (
                    record.name != registration.name
                    or record.description != registration.description
                    or record.normalized_name != _normalized_name(registration.name)
                )
                if not content_changed and not metadata_changed:
                    assert current_revision is not None
                    return AgentRevisionCreateResult(
                        agent=record.to_resource(),
                        revision=current_revision.to_resource(),
                    )

                if created:
                    session.add(record)
                record.name = registration.name
                record.normalized_name = _normalized_name(registration.name)
                record.description = registration.description
                record.updated_by_type = "system"
                record.updated_by_id = registration.system_actor_id
                record.updated_at = now
                if content_changed:
                    record.version = revision.version
                    record.current_revision_id = revision.id
                    session.add(revision)
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action="agent.builtin.register",
                        agent_id=record.id,
                        now=now,
                    )
                )
                await session.flush()
                selected_revision = revision if content_changed else current_revision
                assert selected_revision is not None
                return AgentRevisionCreateResult(
                    agent=record.to_resource(),
                    revision=selected_revision.to_resource(),
                )
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        except IntegrityError as error:
            if expected_content_digest is not None:
                try:
                    replay = await self._builtin_registration_replay(
                        actor=actor,
                        workspace_id=workspace_id,
                        registration=registration,
                        expected_content_digest=expected_content_digest,
                    )
                except AuthorizationError as authorization_error:
                    raise _authorization_error(authorization_error) from authorization_error
                if replay is not None:
                    return replay
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def _builtin_registration_replay(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        registration: BuiltinAgentRegistration,
        expected_content_digest: str,
    ) -> AgentRevisionCreateResult | None:
        async with transaction(self._sessions) as session:
            workspace = await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.agent_create,
            )
            record = await session.scalar(
                select(AgentRecord).where(
                    AgentRecord.id == registration.agent_id,
                    AgentRecord.organization_id == workspace.organization_id,
                    AgentRecord.workspace_id == workspace.workspace_id,
                    AgentRecord.source == AgentSource.builtin.value,
                )
            )
            if (
                record is None
                or record.name != registration.name
                or record.description != registration.description
                or not record.enabled
                or record.archived_at is not None
            ):
                return None
            revision = await session.scalar(
                select(AgentRevisionRecord).where(
                    AgentRevisionRecord.id == record.current_revision_id,
                    AgentRevisionRecord.agent_id == record.id,
                    AgentRevisionRecord.content_digest == expected_content_digest,
                )
            )
            if revision is None:
                return None
            return AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource())

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateAgentRequest,
    ) -> AgentRevisionCreateResult:
        identity = _identity(idempotency_key, request)
        agent_id = new_agent_id()
        revision_id = new_agent_revision_id()
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _revision_create_result_from_revision(session, replay_ref)
                organization_id = workspace.organization_id
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                config=request.config,
            )
        except Exception as error:
            raise resolution_error(error) from error
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.agent_create,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _revision_create_result_from_revision(session, replay_ref)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                record = AgentRecord(
                    id=agent_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    source=AgentSource.custom.value,
                    name=request.name,
                    normalized_name=_normalized_name(request.name),
                    description=request.description,
                    version=1,
                    current_revision_id=revision_id,
                    enabled=True,
                    archived_at=None,
                    duplicated_from_agent_id=None,
                    duplicated_from_revision_id=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                revision = _new_revision(
                    record,
                    revision_id=revision_id,
                    version=1,
                    mode=self._resolver.plugin_runtime_mode,
                    config=request.config,
                    resolved=resolved,
                    source_revision_id=None,
                    actor=actor,
                    now=now,
                )
                session.add_all((record, revision))
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        operation="agent.create",
                        scope_id=workspace_id,
                        identity=identity,
                        result_kind="agent_revision",
                        result_ref=revision_id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        action="agent.create",
                        agent_id=agent_id,
                        now=now,
                    )
                )
                await session.flush()
                return AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource())
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                async with transaction(self._sessions) as session:
                    replay_ref = await _load_replay(
                        session,
                        actor=actor,
                        operation="agent.create",
                        scope_id=workspace_id,
                        identity=identity,
                        now=self._clock(),
                    )
                    if replay_ref is not None:
                        return await _revision_create_result_from_revision(session, replay_ref)
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def get(self, *, actor: AuthenticatedActor, agent_id: str) -> Agent:
        async with transaction(self._sessions) as session:
            workspace = await _authorize_agent(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_read,
            )
            return await _load_agent_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                agent_id=agent_id,
            )

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        enabled: bool | None,
        source: AgentSource | None,
        include_archived: bool,
    ) -> AgentCollection:
        scope = {
            "workspace_id": workspace_id,
            "enabled": enabled,
            "source": source.value if source is not None else None,
            "include_archived": include_archived,
        }
        try:
            after = decode_agent_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentCursorError as error:
            raise AgentError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        try:
            async with transaction(self._sessions) as session:
                authorization = await authorize_agent_collection(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                )
                workspace = authorization.workspace
                query = select(AgentRecord).where(
                    AgentRecord.organization_id == workspace.organization_id,
                    AgentRecord.workspace_id == workspace_id,
                )
                if authorization.visible_agent_ids is not None:
                    query = query.where(AgentRecord.id.in_(authorization.visible_agent_ids))
                if enabled is not None:
                    query = query.where(AgentRecord.enabled == enabled)
                if not include_archived:
                    query = query.where(AgentRecord.archived_at.is_(None))
                if source is not None:
                    query = query.where(AgentRecord.source == source.value)
                if after is not None:
                    updated_at, agent_id = after
                    query = query.where(
                        or_(
                            AgentRecord.updated_at < updated_at,
                            and_(AgentRecord.updated_at == updated_at, AgentRecord.id < agent_id),
                        )
                    )
                rows = tuple(
                    (
                        await session.scalars(
                            query.order_by(AgentRecord.updated_at.desc(), AgentRecord.id.desc()).limit(limit + 1)
                        )
                    ).all()
                )
                page = rows[:limit]
                next_cursor = None
                if len(rows) > limit and page:
                    next_cursor = encode_agent_cursor(
                        updated_at=page[-1].updated_at,
                        agent_id=page[-1].id,
                        scope=scope,
                    )
                return AgentCollection(
                    items=tuple(record.to_resource() for record in page),
                    next_cursor=next_cursor,
                )
        except AuthorizationError as error:
            raise _authorization_error(error) from error

    async def patch_metadata(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        if_match: str,
        request: UpdateAgentRequest,
    ) -> Agent:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_agent(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_update,
                )
                record = await _locked_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                _require_custom_mutable(record)
                _require_etag(record, if_match)
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
                        action="agent.update",
                        agent_id=agent_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def create_revision(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        idempotency_key: str,
        request: CreateAgentRevisionRequest,
    ) -> AgentRevisionCreateResult:
        identity = _identity(idempotency_key, request)
        replay = await self._revision_create_replay(
            actor=actor,
            agent_id=agent_id,
            operation="agent.revision.create",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self.get(actor=actor, agent_id=agent_id)
        _require_custom_resource(current)
        if current.archived_at is not None:
            raise _archived()
        if current.version != request.expected_version:
            raise agent_version_conflict(current.version)
        prepared = await self._prepare_resolution(
            actor=actor,
            agent=current,
            config=request.config,
        )
        return await self._commit_revision_create(
            actor=actor,
            agent_id=agent_id,
            expected_version=request.expected_version,
            operation="agent.revision.create",
            identity=identity,
            prepared=prepared,
            source_revision_id=None,
        )

    async def restore_revision(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        revision_id: str,
        idempotency_key: str,
        request: RestoreAgentRevisionRequest,
    ) -> AgentRevisionCreateResult:
        identity = _identity(idempotency_key, request)
        replay = await self._revision_create_replay(
            actor=actor,
            agent_id=agent_id,
            operation="agent.revision.restore",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self.get(actor=actor, agent_id=agent_id)
        _require_custom_resource(current)
        if current.archived_at is not None:
            raise _archived()
        if current.version != request.expected_version:
            raise agent_version_conflict(current.version)
        prepared_graph = await self._invocation_resolver.prepare_retained_revision_graph(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=revision_id,
            root_state_policy=RootAgentStatePolicy.disabled_allowed,
        )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_agent(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_revision_create,
                )
                record = await _locked_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                _require_custom_mutable(record)
                _require_version(record, request.expected_version)
                source = await _locked_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=revision_id,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent.revision.restore",
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _revision_create_result_from_revision(session, replay_ref)
                await self._invocation_resolver.freeze_retained_revision_graph(
                    session,
                    prepared=prepared_graph,
                )
                restored = _copy_revision(
                    source,
                    revision_id=new_agent_revision_id(),
                    version=record.version + 1,
                    source_revision_id=source.id,
                    agent_id=record.id,
                    actor=actor,
                    now=now,
                )
                session.add(restored)
                record.version = restored.version
                record.current_revision_id = restored.id
                _touch(record, actor=actor, now=now)
                _add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation="agent.revision.restore",
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=restored.id,
                    now=now,
                )
                await session.flush()
                return AgentRevisionCreateResult(agent=record.to_resource(), revision=restored.to_resource())
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._revision_create_replay(
                    actor=actor,
                    agent_id=agent_id,
                    operation="agent.revision.restore",
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

    async def duplicate(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        idempotency_key: str,
        request: DuplicateAgentRequest,
    ) -> Agent:
        identity = _identity(idempotency_key, request)
        replay = await self._duplicate_replay(actor=actor, agent_id=agent_id, identity=identity)
        if replay is not None:
            return replay
        current = await self.get(actor=actor, agent_id=agent_id)
        if current.version != request.expected_version:
            raise agent_version_conflict(current.version)
        if current.archived_at is not None:
            raise _archived()
        prepared_graph = await self._invocation_resolver.prepare_retained_revision_graph(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=current.current_revision_id,
            root_state_policy=RootAgentStatePolicy.disabled_allowed,
        )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                source_workspace = await _authorize_agent(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_read,
                )
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=source_workspace.workspace_id,
                    action=WorkspaceAction.agent_duplicate,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation="agent.duplicate",
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _load_agent_resource(
                        session,
                        organization_id=source_workspace.organization_id,
                        workspace_id=source_workspace.workspace_id,
                        agent_id=replay_ref,
                    )
                source = await _locked_agent(
                    session,
                    source_workspace.organization_id,
                    source_workspace.workspace_id,
                    agent_id,
                )
                _require_version(source, request.expected_version)
                if source.archived_at is not None:
                    raise _archived()
                source_revision = await _locked_revision(
                    session,
                    organization_id=source_workspace.organization_id,
                    workspace_id=source_workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=source.current_revision_id,
                )
                await self._invocation_resolver.freeze_retained_revision_graph(
                    session,
                    prepared=prepared_graph,
                )
                duplicate_agent_id = new_agent_id()
                new_revision_id = new_agent_revision_id()
                duplicate = AgentRecord(
                    id=duplicate_agent_id,
                    organization_id=source.organization_id,
                    workspace_id=source.workspace_id,
                    source=AgentSource.custom.value,
                    name=request.name,
                    normalized_name=_normalized_name(request.name),
                    description=request.description,
                    version=1,
                    current_revision_id=new_revision_id,
                    enabled=True,
                    archived_at=None,
                    duplicated_from_agent_id=source.id,
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
                    version=1,
                    source_revision_id=source_revision.id,
                    agent_id=duplicate_agent_id,
                    actor=actor,
                    now=now,
                )
                session.add_all((duplicate, revision))
                session.add(
                    _evidence(
                        actor=actor,
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        operation="agent.duplicate",
                        scope_id=agent_id,
                        identity=identity,
                        result_kind="agent",
                        result_ref=duplicate.id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        action="agent.duplicate",
                        agent_id=duplicate_agent_id,
                        now=now,
                    )
                )
                await session.flush()
                return duplicate.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._duplicate_replay(actor=actor, agent_id=agent_id, identity=identity)
                if replay is not None:
                    return replay
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def _duplicate_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        identity: tuple[str, str],
    ) -> Agent | None:
        async with transaction(self._sessions) as session:
            source_workspace = await _authorize_agent(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_read,
            )
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=source_workspace.workspace_id,
                action=WorkspaceAction.agent_duplicate,
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation="agent.duplicate",
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return await _load_agent_resource(
                session,
                organization_id=source_workspace.organization_id,
                workspace_id=source_workspace.workspace_id,
                agent_id=replay_ref,
            )

    async def change_lifecycle(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        action: Literal["enable", "disable", "archive", "unarchive"],
        idempotency_key: str,
        if_match: str,
    ) -> Agent:
        operation = f"agent.{action}"
        identity = _identity_payload(idempotency_key, {"action": action})
        replay = await self._agent_command_replay(
            actor=actor,
            agent_id=agent_id,
            operation=operation,
            identity=identity,
            action=WorkspaceAction.agent_lifecycle,
        )
        if replay is not None:
            return replay
        prepared_lifecycle: PreparedAgentRevisionGraph | None = None
        if action in {"enable", "unarchive"}:
            current = await self.get(actor=actor, agent_id=agent_id)
            if action == "enable" and (current.archived_at is not None or current.enabled):
                raise AgentError(
                    "agent_state_conflict",
                    "The Agent cannot be enabled from its current state.",
                    status_code=409,
                )
            if action == "unarchive" and (current.archived_at is None or current.source is not AgentSource.custom):
                raise AgentError(
                    "agent_state_conflict",
                    "The Agent cannot be unarchived from its current state.",
                    status_code=409,
                )
            prepared_lifecycle = await self._invocation_resolver.prepare_retained_revision_graph(
                actor=actor,
                agent_id=agent_id,
                root_state_policy=(
                    RootAgentStatePolicy.archived_allowed
                    if action == "unarchive"
                    else RootAgentStatePolicy.disabled_allowed
                ),
            )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_agent(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_lifecycle,
                )
                record = await _locked_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                _require_etag(record, if_match)
                _apply_lifecycle_transition(session, record, action=action, now=now)
                if prepared_lifecycle is not None:
                    await self._invocation_resolver.freeze_retained_revision_graph(
                        session,
                        prepared=prepared_lifecycle,
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
                        scope_id=agent_id,
                        identity=identity,
                        result_kind="agent",
                        result_ref=agent_id,
                        now=now,
                    )
                )
                session.add(
                    _audit(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        action=operation,
                        agent_id=agent_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._agent_command_replay(
                    actor=actor,
                    agent_id=agent_id,
                    operation=operation,
                    identity=identity,
                    action=WorkspaceAction.agent_lifecycle,
                )
                if replay is not None:
                    return replay
            raise

    async def list_revisions(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        limit: int,
        cursor: str | None,
    ) -> AgentRevisionCollection:
        scope: dict[str, object] = {"agent_id": agent_id}
        try:
            after = decode_revision_cursor(cursor, scope=scope) if cursor is not None else None
        except AgentCursorError as error:
            raise AgentError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize_agent(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_read,
            )
            query = select(AgentRevisionRecord).where(
                AgentRevisionRecord.organization_id == workspace.organization_id,
                AgentRevisionRecord.workspace_id == workspace.workspace_id,
                AgentRevisionRecord.agent_id == agent_id,
            )
            if after is not None:
                number, revision_id = after
                query = query.where(
                    or_(
                        AgentRevisionRecord.version < number,
                        and_(
                            AgentRevisionRecord.version == number,
                            AgentRevisionRecord.id < revision_id,
                        ),
                    )
                )
            rows = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            AgentRevisionRecord.version.desc(),
                            AgentRevisionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = rows[:limit]
            next_cursor = None
            if len(rows) > limit and page:
                next_cursor = encode_revision_cursor(
                    version=page[-1].version,
                    revision_id=page[-1].id,
                    scope=scope,
                )
            return AgentRevisionCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get_revision(
        self,
        *,
        actor: AuthenticatedActor,
        revision_id: str,
        agent_id: str | None = None,
    ) -> AgentRevision:
        async with transaction(self._sessions) as session:
            revision = await session.scalar(select(AgentRevisionRecord).where(AgentRevisionRecord.id == revision_id))
            if revision is None or (agent_id is not None and revision.agent_id != agent_id):
                raise agent_revision_not_found()
            await _authorize_agent(
                session,
                actor=actor,
                agent_id=revision.agent_id,
                action=WorkspaceAction.agent_read,
            )
            return revision.to_resource()

    async def _prepare_resolution(
        self, *, actor: AuthenticatedActor, agent: Agent, config: AgentConfig
    ) -> PreparedRevisionResolution:
        try:
            return await self._resolver.prepare(
                actor=actor,
                organization_id=agent.organization_id,
                workspace_id=agent.workspace_id,
                agent_id=agent.id,
                config=config,
            )
        except Exception as error:
            raise resolution_error(error) from error

    async def _commit_revision_create(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        expected_version: int,
        operation: str,
        identity: tuple[str, str],
        prepared: PreparedRevisionResolution,
        source_revision_id: str | None,
    ) -> AgentRevisionCreateResult:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_agent(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_revision_create,
                )
                replay_ref = await _load_replay(
                    session,
                    actor=actor,
                    operation=operation,
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await _revision_create_result_from_revision(session, replay_ref)
                record = await _locked_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                _require_custom_mutable(record)
                _require_version(record, expected_version)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                revision = _new_revision(
                    record,
                    revision_id=new_agent_revision_id(),
                    version=record.version + 1,
                    mode=self._resolver.plugin_runtime_mode,
                    config=prepared.config,
                    resolved=resolved,
                    source_revision_id=source_revision_id,
                    actor=actor,
                    now=now,
                )
                current = await _locked_revision(
                    session,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    agent_id=record.id,
                    revision_id=record.current_revision_id,
                )
                if current.content_digest == revision.content_digest:
                    revision = current
                else:
                    session.add(revision)
                    record.version = revision.version
                    record.current_revision_id = revision.id
                    _touch(record, actor=actor, now=now)
                _add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation=operation,
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=revision.id,
                    now=now,
                )
                await session.flush()
                return AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource())
        except AuthorizationError as error:
            raise _authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if _is_idempotency_race(error):
                replay = await self._revision_create_replay(
                    actor=actor,
                    agent_id=agent_id,
                    operation=operation,
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

    async def _revision_create_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        operation: str,
        identity: tuple[str, str],
    ) -> AgentRevisionCreateResult | None:
        async with transaction(self._sessions) as session:
            await _authorize_agent(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_revision_create,
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            return None if replay_ref is None else await _revision_create_result_from_revision(session, replay_ref)

    async def _agent_command_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        operation: str,
        identity: tuple[str, str],
        action: WorkspaceAction,
    ) -> Agent | None:
        async with transaction(self._sessions) as session:
            workspace = await _authorize_agent(
                session,
                actor=actor,
                agent_id=agent_id,
                action=action,
            )
            replay_ref = await _load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return await _load_agent_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                agent_id=replay_ref,
            )

    async def _replay_agent(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        operation: str,
        scope_id: str,
        identity: tuple[str, str],
    ) -> Agent:
        async with transaction(self._sessions) as session:
            workspace = await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.agent_create,
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
            return await _load_agent_resource(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                agent_id=result_ref,
            )


async def _authorize_agent(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_agent(
            session,
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            agent_id=agent_id,
            action=action,
        )
    except AuthorizationError as error:
        raise _authorization_error(error, exact=True) from error


async def _load_agent_resource(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
) -> Agent:
    record = await session.scalar(
        select(AgentRecord).where(
            AgentRecord.id == agent_id,
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
        )
    )
    if record is None:
        raise agent_not_found()
    return record.to_resource()


async def _locked_agent(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
) -> AgentRecord:
    record = await session.scalar(
        select(AgentRecord)
        .where(
            AgentRecord.id == agent_id,
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None:
        raise agent_not_found()
    return record


async def _locked_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
) -> AgentRevisionRecord:
    record = await session.scalar(
        select(AgentRevisionRecord)
        .where(
            AgentRevisionRecord.id == revision_id,
            AgentRevisionRecord.agent_id == agent_id,
            AgentRevisionRecord.organization_id == organization_id,
            AgentRevisionRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None:
        raise agent_revision_not_found()
    return record


def _new_builtin_agent(
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
    registration: BuiltinAgentRegistration,
    now: datetime,
) -> AgentRecord:
    return AgentRecord(
        id=registration.agent_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        source=AgentSource.builtin.value,
        name=registration.name,
        normalized_name=_normalized_name(registration.name),
        description=registration.description,
        version=1,
        current_revision_id=revision_id,
        enabled=True,
        archived_at=None,
        duplicated_from_agent_id=None,
        duplicated_from_revision_id=None,
        created_by_type="system",
        created_by_id=registration.system_actor_id,
        updated_by_type="system",
        updated_by_id=registration.system_actor_id,
        created_at=now,
        updated_at=now,
    )


def _new_revision(
    agent: AgentRecord,
    *,
    revision_id: str,
    version: int,
    mode: PluginRuntimeMode,
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
    source_revision_id: str | None,
    actor: AuthenticatedActor,
    now: datetime,
) -> AgentRevisionRecord:
    config_payload = config.model_dump(mode="json", by_alias=True)
    resolved_payload = resolved.model_dump(mode="json", by_alias=True)
    content_digest = canonical_digest(
        {
            "plugin_runtime_mode": mode.value,
            "config": config_payload,
            "resolved": resolved_payload,
        }
    )
    return AgentRevisionRecord(
        id=revision_id,
        organization_id=agent.organization_id,
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        version=version,
        plugin_runtime_mode=mode.value,
        config=config_payload,
        config_digest=canonical_digest(config),
        resolved_model=resolved.resolved_model.model_dump(mode="json"),
        resolved_plugin_versions=[item.model_dump(mode="json") for item in resolved.resolved_plugin_versions],
        runtime_lock_digest=resolved.runtime_lock_digest,
        resolved_skills=[item.model_dump(mode="json") for item in resolved.resolved_skills],
        connector_tools=[item.model_dump(mode="json") for item in resolved.connector_tools],
        mcp_tools=[item.model_dump(mode="json") for item in resolved.mcp_tools],
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
    source: AgentRevisionRecord,
    *,
    revision_id: str,
    version: int,
    source_revision_id: str,
    actor: AuthenticatedActor,
    now: datetime,
    agent_id: str | None = None,
) -> AgentRevisionRecord:
    return AgentRevisionRecord(
        id=revision_id,
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        agent_id=agent_id or source.agent_id,
        version=version,
        plugin_runtime_mode=source.plugin_runtime_mode,
        config=source.config,
        config_digest=source.config_digest,
        resolved_model=source.resolved_model,
        resolved_plugin_versions=source.resolved_plugin_versions,
        runtime_lock_digest=source.runtime_lock_digest,
        resolved_skills=source.resolved_skills,
        connector_tools=source.connector_tools,
        mcp_tools=source.mcp_tools,
        resolved_environment=source.resolved_environment,
        resolved_subagents=source.resolved_subagents,
        content_digest=source.content_digest,
        source_revision_id=source_revision_id,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


async def _revision_create_result_from_revision(session: AsyncSession, revision_id: str) -> AgentRevisionCreateResult:
    revision = await session.scalar(select(AgentRevisionRecord).where(AgentRevisionRecord.id == revision_id))
    if revision is None:
        raise _idempotency_conflict()
    agent = await _load_agent_resource(
        session,
        organization_id=revision.organization_id,
        workspace_id=revision.workspace_id,
        agent_id=revision.agent_id,
    )
    return AgentRevisionCreateResult(agent=agent, revision=revision.to_resource())


def _require_custom_resource(agent: Agent) -> None:
    if agent.source is AgentSource.builtin:
        raise AgentError("agent_state_conflict", "Built-in Agents are read-only.", status_code=409)


def _builtin_identity_conflict() -> AgentError:
    return AgentError(
        "agent_state_conflict",
        "The built-in Agent identity is already in use.",
        status_code=409,
    )


def _require_custom_mutable(record: AgentRecord) -> None:
    if record.source != AgentSource.custom.value:
        raise AgentError("agent_state_conflict", "Built-in Agents are read-only.", status_code=409)
    if record.archived_at is not None:
        raise _archived()


def _require_version(record: AgentRecord, expected: int) -> None:
    if record.version != expected:
        raise agent_version_conflict(record.version)


def _require_etag(record: AgentRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise AgentError(
            "precondition_failed",
            "The Agent representation has changed.",
            status_code=412,
            details={"current_etag": current},
        )


def _touch(record: AgentRecord, *, actor: AuthenticatedActor, now: datetime) -> None:
    record.updated_by_type = actor.principal.principal_type.value
    record.updated_by_id = actor.principal.principal_id
    record.updated_at = now


def _apply_lifecycle_transition(
    _session: AsyncSession,
    record: AgentRecord,
    *,
    action: Literal["enable", "disable", "archive", "unarchive"],
    now: datetime,
) -> None:
    if action == "enable":
        if record.archived_at is not None or record.enabled:
            raise AgentError(
                "agent_state_conflict", "The Agent cannot be enabled from its current state.", status_code=409
            )
        record.enabled = True
        return
    if action == "disable":
        if record.archived_at is not None or not record.enabled:
            raise AgentError(
                "agent_state_conflict", "The Agent cannot be disabled from its current state.", status_code=409
            )
        record.enabled = False
        return
    if action == "archive":
        if record.source != AgentSource.custom.value or record.enabled or record.archived_at is not None:
            raise AgentError("agent_state_conflict", "Only a disabled custom Agent can be archived.", status_code=409)
        record.archived_at = now
        return
    if record.archived_at is None or record.source != AgentSource.custom.value:
        raise AgentError(
            "agent_state_conflict", "The Agent cannot be unarchived from its current state.", status_code=409
        )
    record.archived_at = None
    record.enabled = False


async def _require_not_in_use(session: AsyncSession, target: AgentRecord) -> None:
    current_revisions = tuple(
        (
            await session.scalars(
                select(AgentRevisionRecord)
                .join(AgentRecord, AgentRecord.current_revision_id == AgentRevisionRecord.id)
                .where(
                    AgentRecord.organization_id == target.organization_id,
                    AgentRecord.workspace_id == target.workspace_id,
                    AgentRecord.enabled.is_(True),
                    AgentRecord.archived_at.is_(None),
                    AgentRecord.id != target.id,
                )
            )
        ).all()
    )
    pending = list(current_revisions)
    visited: set[str] = set()
    while pending:
        revision = pending.pop()
        if revision.id in visited:
            continue
        visited.add(revision.id)
        child_revision_ids: list[str] = []
        for edge in revision.resolved_subagents:
            if edge.get("child_agent_id") == target.id:
                raise AgentError(
                    "agent_in_use",
                    "The Agent is referenced by an enabled Agent graph.",
                    status_code=409,
                )
            child_revision_id = edge.get("child_agent_revision_id")
            if isinstance(child_revision_id, str) and child_revision_id not in visited:
                child_revision_ids.append(child_revision_id)
        if child_revision_ids:
            pending.extend(
                (
                    await session.scalars(
                        select(AgentRevisionRecord).where(
                            AgentRevisionRecord.organization_id == target.organization_id,
                            AgentRevisionRecord.workspace_id == target.workspace_id,
                            AgentRevisionRecord.id.in_(child_revision_ids),
                        )
                    )
                ).all()
            )


def _add_command_evidence_and_audit(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: AgentRecord,
    operation: str,
    identity: tuple[str, str],
    result_kind: str,
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
            result_kind=result_kind,
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
            agent_id=record.id,
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


def _identity_payload(idempotency_key: str, payload: JsonObject) -> tuple[str, str]:
    try:
        encoded = idempotency_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if not 1 <= len(encoded) <= _MAX_IDEMPOTENCY_KEY_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise _invalid_idempotency_key()
    return hashlib.sha256(encoded).hexdigest(), canonical_digest(payload)


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
    agent_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("audit"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="agent",
        resource_id=agent_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def _normalized_name(value: str) -> str:
    return value.casefold()


def _authorization_error(error: AuthorizationError, *, exact: bool = False) -> AgentError:
    if exact or error.concealed:
        return agent_not_found()
    return AgentError("forbidden", "The operation is not allowed.", status_code=403)


def _archived() -> AgentError:
    return AgentError("agent_archived", "The Agent is archived.", status_code=409)


def _invalid_idempotency_key() -> AgentError:
    return AgentError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def _idempotency_conflict() -> AgentError:
    return AgentError(
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
