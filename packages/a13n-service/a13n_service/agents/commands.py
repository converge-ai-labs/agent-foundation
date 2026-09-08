"""Agent creation, metadata, and lifecycle commands."""

from __future__ import annotations

from typing import Literal

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
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .domain import (
    Agent,
    AgentRevisionCreateResult,
    AgentSource,
    CreateAgentRequest,
    UpdateAgentRequest,
    new_agent_id,
    new_agent_revision_id,
)
from .errors import (
    AgentError,
    map_authorization_error,
)
from .invocation_resolution import AgentInvocationResolver, PreparedAgentInvocation, RootAgentStatePolicy
from .models import AgentRecord
from .persistence import (
    agent_name_key,
    apply_lifecycle_transition,
    authorize_agent_scope,
    load_replay,
    lock_agent,
    new_agent_audit,
    new_revision,
    payload_identity,
    request_identity,
    require_custom_mutable,
    require_etag,
    require_not_in_use,
    touch_agent,
)
from .queries import AgentQueries
from .resolution import AgentResolver, resolution_error


class AgentCommands:
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

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateAgentRequest,
    ) -> AgentRevisionCreateResult:
        identity = request_identity(idempotency_key, request)
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
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation="agent.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return replay_ref.restore(AgentRevisionCreateResult)
                organization_id = workspace.organization_id
        except AuthorizationError as error:
            raise map_authorization_error(error) from error
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
                replay_ref = await load_replay(
                    session,
                    actor=actor,
                    operation="agent.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay_ref is not None:
                    return replay_ref.restore(AgentRevisionCreateResult)
                try:
                    resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
                except Exception as error:
                    raise resolution_error(error) from error
                await authorize_template(
                    session, actor=actor, workspace_id=workspace_id, template_id=request.default_environment_template_id
                )
                record = AgentRecord(
                    id=agent_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    source=AgentSource.custom.value,
                    default_environment_template_id=request.default_environment_template_id,
                    name=request.name,
                    normalized_name=agent_name_key(request.name),
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
                revision = new_revision(
                    record,
                    revision_id=revision_id,
                    version=1,
                    config=request.config,
                    resolved=resolved,
                    source_revision_id=None,
                    actor=actor,
                    now=now,
                )
                session.add_all((record, revision))
                session.add(
                    evidence_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        operation="agent.create",
                        scope_id=workspace_id,
                        identity=identity,
                        result_kind="agent_revision",
                        result_ref=revision_id,
                        now=now,
                        response=AgentRevisionCreateResult(agent=record.to_resource(), revision=revision.to_resource()),
                    )
                )
                session.add(
                    new_agent_audit(
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
            raise map_authorization_error(error) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                async with transaction(self._sessions) as session:
                    replay_ref = await load_replay(
                        session,
                        actor=actor,
                        operation="agent.create",
                        scope_id=workspace_id,
                        identity=identity,
                        now=self._clock(),
                    )
                    if replay_ref is not None:
                        return replay_ref.restore(AgentRevisionCreateResult)
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                category=ErrorCategory.conflict,
            ) from error

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
                workspace = await authorize_agent_scope(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_update,
                )
                record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                require_custom_mutable(record)
                require_etag(record, if_match)
                if "name" in request.model_fields_set:
                    assert request.name is not None
                    record.name = request.name
                    record.normalized_name = agent_name_key(request.name)
                if "description" in request.model_fields_set:
                    record.description = request.description
                if "default_environment_template_id" in request.model_fields_set:
                    await authorize_template(
                        session,
                        actor=actor,
                        workspace_id=workspace.workspace_id,
                        template_id=request.default_environment_template_id,
                    )
                    record.default_environment_template_id = request.default_environment_template_id
                touch_agent(record, actor=actor, now=now)
                session.add(
                    new_agent_audit(
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
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            raise AgentError(
                "agent_name_conflict",
                "An Agent with this name already exists in the Workspace.",
                category=ErrorCategory.conflict,
            ) from error

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
        identity = payload_identity(idempotency_key, {"action": action})
        replay = await self._agent_command_replay(
            actor=actor,
            agent_id=agent_id,
            operation=operation,
            identity=identity,
            action=WorkspaceAction.agent_lifecycle,
        )
        if replay is not None:
            return replay
        prepared_lifecycle: PreparedAgentInvocation | None = None
        if action in {"enable", "unarchive"}:
            current = await self._queries.get(actor=actor, agent_id=agent_id)
            if action == "enable" and (current.archived_at is not None or current.enabled):
                raise AgentError(
                    "agent_state_conflict",
                    "The Agent cannot be enabled from its current state.",
                    category=ErrorCategory.conflict,
                )
            if action == "unarchive" and (current.archived_at is None or current.source is not AgentSource.custom):
                raise AgentError(
                    "agent_state_conflict",
                    "The Agent cannot be unarchived from its current state.",
                    category=ErrorCategory.conflict,
                )
            prepared_lifecycle = await self._invocation_resolver.preparation.prepare(
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
                workspace = await authorize_agent_scope(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_lifecycle,
                )
                record = await lock_agent(session, workspace.organization_id, workspace.workspace_id, agent_id)
                require_etag(record, if_match)
                apply_lifecycle_transition(record, action=action, now=now)
                if prepared_lifecycle is not None:
                    await self._invocation_resolver.freezing.freeze_in_transaction(
                        session,
                        prepared=prepared_lifecycle,
                    )
                if action == "disable":
                    await require_not_in_use(session, record)
                touch_agent(record, actor=actor, now=now)
                session.add(
                    evidence_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        operation=operation,
                        scope_id=agent_id,
                        identity=identity,
                        result_kind="agent",
                        result_ref=agent_id,
                        now=now,
                        response=record.to_resource(),
                    )
                )
                session.add(
                    new_agent_audit(
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
            raise map_authorization_error(error, exact=True) from error
        except IntegrityError as error:
            if is_evidence_unique_race(error):
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

    async def _agent_command_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        operation: str,
        identity: IdempotencyIdentity,
        action: WorkspaceAction,
    ) -> Agent | None:
        async with transaction(self._sessions) as session:
            await authorize_agent_scope(
                session,
                actor=actor,
                agent_id=agent_id,
                action=action,
            )
            replay_ref = await load_replay(
                session,
                actor=actor,
                operation=operation,
                scope_id=agent_id,
                identity=identity,
                now=self._clock(),
            )
            if replay_ref is None:
                return None
            return replay_ref.restore(Agent)
