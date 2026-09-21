"""Immutable Agent Revision commands."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    is_evidence_unique_race,
)
from a13n_service.environments.authoring import authorize_template
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
    SetDefaultAgentRevisionRequest,
)
from .errors import (
    agent_archived,
    map_authorization_error,
)
from .invocation_resolution import AgentInvocationResolver, RootAgentStatePolicy
from .persistence import (
    add_command_evidence_and_audit,
    authorize_agent_scope,
    load_replay,
    load_revision_create_result,
    lock_agent,
    lock_revision,
    request_identity,
    require_custom,
    require_custom_mutable,
    require_etag,
    touch_agent,
)
from .publication import publish_revision
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
        if_match: str,
    ) -> AgentRevisionCreateResult:
        identity = request_identity(idempotency_key)
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
        prepared = await self._prepare_resolution(
            actor=actor,
            agent=current,
            config=request.config,
        )
        return await self._commit_revision_create(
            actor=actor,
            agent_id=agent_id,
            if_match=if_match,
            operation="agent.revision.create",
            identity=identity,
            prepared=prepared,
            source_revision_id=None,
            change_summary=request.change_summary,
        )

    async def set_default_revision(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        revision_id: str,
        idempotency_key: str,
        request: SetDefaultAgentRevisionRequest,
        if_match: str,
    ) -> AgentRevisionCreateResult:
        identity = request_identity(idempotency_key)
        replay = await self._revision_create_replay(
            actor=actor,
            agent_id=agent_id,
            operation="agent.revision.set_default",
            identity=identity,
        )
        if replay is not None:
            return replay
        current = await self._queries.get(actor=actor, agent_id=agent_id)
        require_custom(current)
        if current.archived_at is not None:
            raise agent_archived()
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
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation="agent.revision.set_default",
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await load_revision_create_result(session, replay_ref.result_ref)
                require_custom_mutable(record)
                require_etag(record, if_match)
                source = await lock_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    agent_id=agent_id,
                    revision_id=revision_id,
                )
                await authorize_template(
                    session,
                    actor=actor,
                    workspace_id=record.workspace_id,
                    template_id=source.config.get("default_environment_template_id"),
                )
                await self._invocation_resolver.freezing.freeze_in_transaction(
                    session,
                    prepared=prepared_graph,
                )
                prior_id = record.default_revision_id
                if prior_id != source.id:
                    record.default_revision_id = source.id
                    touch_agent(record, actor=actor, now=now)
                result = AgentRevisionCreateResult(agent=record.to_resource(), revision=source.to_resource())
                add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation="agent.revision.set_default",
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=source.id,
                    now=now,
                    audit_details={"from_revision_id": prior_id, "to_revision_id": source.id}
                    if prior_id != source.id
                    else None,
                    audit=prior_id != source.id,
                )
                await session.flush()
                return result
        except AuthorizationError as error:
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                replay = await self._revision_create_replay(
                    actor=actor,
                    agent_id=agent_id,
                    operation="agent.revision.set_default",
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
        if_match: str,
        operation: str,
        identity: IdempotencyIdentity,
        prepared: PreparedRevisionResolution,
        source_revision_id: str | None,
        change_summary: str | None,
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
                record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation=operation,
                    scope_id=agent_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return await load_revision_create_result(session, replay_ref.result_ref)
                require_custom_mutable(record)
                require_etag(record, if_match)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                publication = await publish_revision(
                    session,
                    locked_agent=record,
                    config=prepared.config,
                    resolved=resolved,
                    source_revision_id=source_revision_id,
                    change_summary=change_summary,
                    actor=actor,
                    now=now,
                )
                revision = publication.revision
                add_command_evidence_and_audit(
                    session,
                    actor=actor,
                    record=record,
                    operation=operation,
                    identity=identity,
                    result_kind="agent_revision",
                    result_ref=revision.id,
                    now=now,
                    audit_details={"from_revision_id": publication.previous_revision_id, "to_revision_id": revision.id}
                    if publication.changed
                    else None,
                    audit=publication.changed,
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
            return None if replay_ref is None else await load_revision_create_result(session, replay_ref.result_ref)
