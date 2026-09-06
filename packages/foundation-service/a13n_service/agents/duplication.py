"""Agent duplication use case."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    is_evidence_unique_race,
)
from a13n_service.durable_operations.requests import evidence_record
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_workspace,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import (
    Agent,
    AgentSource,
    DuplicateAgentRequest,
    new_agent_id,
    new_agent_revision_id,
)
from .errors import (
    AgentError,
    agent_archived,
    agent_version_conflict,
    map_authorization_error,
)
from .invocation_resolution import AgentInvocationResolver, RootAgentStatePolicy
from .models import AgentRecord
from .persistence import (
    authorize_agent_scope,
    copy_revision,
    load_replay,
    lock_agent,
    lock_revision,
    new_agent_audit,
    normalize_agent_name,
    request_identity,
    require_version,
)
from .queries import AgentQueries


class AgentDuplication:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocation_resolver: AgentInvocationResolver,
        queries: AgentQueries,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._invocation_resolver = invocation_resolver
        self._queries = queries
        self._clock = clock

    async def duplicate(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        idempotency_key: str,
        request: DuplicateAgentRequest,
    ) -> Agent:
        identity = request_identity(idempotency_key, request)
        replay = await self._duplicate_replay(actor=actor, agent_id=agent_id, identity=identity)
        if replay is not None:
            return replay
        current = await self._queries.get(actor=actor, agent_id=agent_id)
        if current.version != request.expected_version:
            raise agent_version_conflict(current.version)
        if current.archived_at is not None:
            raise agent_archived()
        prepared_graph = await self._invocation_resolver.preparation.prepare_retained_revision_graph(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=current.current_revision_id,
            root_state_policy=RootAgentStatePolicy.disabled_allowed,
        )
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                source_workspace = await authorize_agent_scope(
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
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation="agent.duplicate",
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return replay_ref.restore(Agent)
                source = await lock_agent(
                    session,
                    source_workspace.organization_id,
                    source_workspace.workspace_id,
                    agent_id,
                )
                require_version(source, request.expected_version)
                if source.archived_at is not None:
                    raise agent_archived()
                source_revision = await lock_revision(
                    session,
                    organization_id=source_workspace.organization_id,
                    workspace_id=source_workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=source.current_revision_id,
                )
                await self._invocation_resolver.freezing.freeze_retained_revision_graph(
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
                    normalized_name=normalize_agent_name(request.name),
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
                revision = copy_revision(
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
                    evidence_record(
                        actor=actor,
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        operation="agent.duplicate",
                        scope_id=agent_id,
                        identity=identity,
                        result_kind="agent",
                        result_ref=duplicate.id,
                        now=now,
                        response=duplicate.to_resource(),
                    )
                )
                session.add(
                    new_agent_audit(
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
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                replay = await self._duplicate_replay(actor=actor, agent_id=agent_id, identity=identity)
                if replay is not None:
                    return replay
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                category=ErrorCategory.conflict,
            ) from error

    async def _duplicate_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        identity: IdempotencyIdentity,
    ) -> Agent | None:
        async with transaction(self._sessions) as session:
            source_workspace = await authorize_agent_scope(
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
            replay_ref = await load_replay(
                session,
                actor=actor,
                operation="agent.duplicate",
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return replay_ref.restore(Agent)
