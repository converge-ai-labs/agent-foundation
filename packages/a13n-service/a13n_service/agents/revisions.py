"""Immutable Agent Revision commands."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    is_evidence_unique_race,
)
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import (
    Agent,
    AgentConfig,
    AgentRevisionCreateResult,
    CreateAgentRevisionRequest,
    RestoreAgentRevisionRequest,
    new_agent_revision_id,
)
from .errors import (
    agent_archived,
    agent_version_conflict,
    map_authorization_error,
)
from .invocation_resolution import AgentInvocationResolver, RootAgentStatePolicy
from .persistence import (
    add_command_evidence_and_audit,
    authorize_agent_scope,
    copy_revision,
    load_replay,
    lock_agent,
    lock_revision,
    new_revision,
    request_identity,
    require_custom,
    require_custom_mutable,
    require_version,
    touch_agent,
)
from .queries import AgentQueries
from .resolution import AgentResolver, PreparedRevisionResolution, resolution_error


class AgentRevisions:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        invocation_resolver: AgentInvocationResolver,
        queries: AgentQueries,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._invocation_resolver = invocation_resolver
        self._queries = queries
        self._clock = clock

    async def create_revision(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        idempotency_key: str,
        request: CreateAgentRevisionRequest,
    ) -> AgentRevisionCreateResult:
        identity = request_identity(idempotency_key, request)
        replay = await self._revision_create_replay(
            actor=actor,
            agent_id=agent_id,
            operation="agent.revision.create",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self._queries.get(actor=actor, agent_id=agent_id)
        require_custom(current)
        if current.archived_at is not None:
            raise agent_archived()
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
        identity = request_identity(idempotency_key, request)
        replay = await self._revision_create_replay(
            actor=actor,
            agent_id=agent_id,
            operation="agent.revision.restore",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self._queries.get(actor=actor, agent_id=agent_id)
        require_custom(current)
        if current.archived_at is not None:
            raise agent_archived()
        if current.version != request.expected_version:
            raise agent_version_conflict(current.version)
        prepared_graph = await self._invocation_resolver.preparation.prepare(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=revision_id,
            root_state_policy=RootAgentStatePolicy.disabled_allowed,
        )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_agent_scope(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_revision_create,
                )
                record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                require_custom_mutable(record)
                require_version(record, request.expected_version)
                source = await lock_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=revision_id,
                )
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation="agent.revision.restore",
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return replay_ref.restore(AgentRevisionCreateResult)
                await self._invocation_resolver.freezing.freeze_in_transaction(
                    session,
                    prepared=prepared_graph,
                )
                restored = copy_revision(
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
                touch_agent(record, actor=actor, now=now)
                add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation="agent.revision.restore",
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=restored.id,
                    now=now,
                    response=AgentRevisionCreateResult(agent=record.to_resource(), revision=restored.to_resource()),
                )
                await session.flush()
                return AgentRevisionCreateResult(agent=record.to_resource(), revision=restored.to_resource())
        except AuthorizationError as error:
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                replay = await self._revision_create_replay(
                    actor=actor,
                    agent_id=agent_id,
                    operation="agent.revision.restore",
                    identity=identity,
                )
                if replay is not None:
                    return replay
            raise

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
        identity: IdempotencyIdentity,
        prepared: PreparedRevisionResolution,
        source_revision_id: str | None,
    ) -> AgentRevisionCreateResult:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_agent_scope(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_revision_create,
                )
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation=operation,
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return replay_ref.restore(AgentRevisionCreateResult)
                record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                require_custom_mutable(record)
                require_version(record, expected_version)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                revision = new_revision(
                    record,
                    revision_id=new_agent_revision_id(),
                    version=record.version + 1,
                    config=prepared.config,
                    resolved=resolved,
                    source_revision_id=source_revision_id,
                    actor=actor,
                    now=now,
                )
                assert record.current_revision_id is not None
                current = await lock_revision(
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
                    touch_agent(record, actor=actor, now=now)
                add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation=operation,
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=revision.id,
                    now=now,
                    response=AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource()),
                )
                await session.flush()
                return AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource())
        except AuthorizationError as error:
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
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
        identity: IdempotencyIdentity,
    ) -> AgentRevisionCreateResult | None:
        async with transaction(self._sessions) as session:
            await authorize_agent_scope(
                session,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.agent_revision_create,
            )
            replay_ref = await load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            return None if replay_ref is None else replay_ref.restore(AgentRevisionCreateResult)
