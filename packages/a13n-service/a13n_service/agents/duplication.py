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
from a13n_service.environments.authoring import authorize_template
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_workspace,
)
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.labels import merge_labels
from a13n_service.resource_keys import insert_with_key
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
    request_identity,
    require_etag,
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
        if_match: str,
    ) -> Agent:
        identity = request_identity(idempotency_key, request)
        replay = await self._duplicate_replay(actor=actor, agent_id=agent_id, identity=identity)
        if replay is not None:
            return replay
        current = await self._queries.get(actor=actor, agent_id=agent_id)
        if current.archived_at is not None:
            raise agent_archived()
        prepared_graph = await self._invocation_resolver.preparation.prepare(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=current.default_revision_id,
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
                source = await lock_agent(
                    session,
                    source_workspace.organization_id,
                    source_workspace.workspace_id,
                    agent_id,
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
                require_etag(source, if_match)
                if source.archived_at is not None:
                    raise agent_archived()
                assert source.default_revision_id is not None
                source_revision = await lock_revision(
                    session,
                    organization_id=source_workspace.organization_id,
                    workspace_id=source_workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=source.default_revision_id,
                )
                await authorize_template(
                    session,
                    actor=actor,
                    workspace_id=source_workspace.workspace_id,
                    template_id=source_revision.config.get("default_environment_template_id"),
                )
                await self._invocation_resolver.freezing.freeze_in_transaction(
                    session,
                    prepared=prepared_graph,
                )
                duplicate_agent_id = new_agent_id()
                new_revision_id = new_agent_revision_id()
                try:
                    labels = merge_labels(source.labels, request.labels)
                except ValueError as error:
                    raise AgentError(
                        "merged_labels_invalid",
                        str(error),
                        category=ErrorCategory.invalid_request,
                    ) from error
                duplicate = AgentRecord(
                    id=duplicate_agent_id,
                    organization_id=source.organization_id,
                    workspace_id=source.workspace_id,
                    source=AgentSource.custom.value,
                    name=request.name,
                    description=request.description,
                    labels=labels,
                    default_revision_id=new_revision_id,
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
                await insert_with_key(session, duplicate, prefix="agent", requested=request.key)
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
                        details={"from_revision_id": None, "to_revision_id": revision.id},
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
            raise

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
