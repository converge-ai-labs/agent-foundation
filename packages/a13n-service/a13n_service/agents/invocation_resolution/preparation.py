"""Transaction-free preparation for Agent invocation resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionResolver,
)
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.storage import short_session

from ..connectivity_resolution import prepare_invocation_connectivity
from ..domain import (
    AgentRevision,
    AgentRunOverride,
    ResolvedSubagentEdge,
)
from ..errors import (
    agent_current_revision_missing,
    agent_revision_not_executable,
    current_revision_conflict,
    map_authorization_error,
    map_model_error,
)
from ..invocation import merge_agent_run_override
from ..resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES
from ..validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config
from .contracts import (
    AgentSelectorKind,
    PreparedAgentInvocation,
    PreparedChildInvocation,
    RootAgentStatePolicy,
)
from .queries import (
    load_agent_record,
    load_revision_record,
    require_invocable_agent,
    select_child_revision_id,
)
from .skills import prepare_skills


@dataclass(slots=True)
class _GraphBudget:
    remaining: int = MAX_SUBAGENT_NODES


class AgentInvocationPreparer:
    """Resolve mutable inputs without holding a database transaction across I/O."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver,
        protocol_policy: AgentProtocolPolicy,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver
        self._protocol_policy = protocol_policy

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        agent_revision_id: str | None = None,
        expected_current_revision_id: str | None = None,
        config_override: AgentRunOverride | None = None,
        root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
        _active_agents: tuple[str, ...] = (),
        _budget: _GraphBudget | None = None,
    ) -> PreparedAgentInvocation:
        budget = _budget or _GraphBudget()
        budget.remaining -= 1
        if budget.remaining < 0:
            raise agent_revision_not_executable("subagent_graph_too_large")
        if agent_id in _active_agents:
            raise agent_revision_not_executable("subagent_cycle")
        if len(_active_agents) > MAX_SUBAGENT_DEPTH:
            raise agent_revision_not_executable("subagent_graph_too_deep")
        workspace_id = actor.workspace_id
        try:
            async with short_session(self._sessions) as session:
                authorized = await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_invoke,
                )
                agent = await load_agent_record(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    for_update=False,
                )
                require_invocable_agent(agent, policy=root_state_policy)
                if (
                    expected_current_revision_id is not None
                    and agent.current_revision_id != expected_current_revision_id
                ):
                    raise current_revision_conflict(agent.current_revision_id)
                selector_kind = AgentSelectorKind.exact if agent_revision_id is not None else AgentSelectorKind.current
                revision_id = agent_revision_id or agent.current_revision_id
                if revision_id is None:
                    raise agent_current_revision_missing()
                revision_record = await load_revision_record(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    revision_id=revision_id,
                    for_update=False,
                )
                revision = revision_record.to_resource()
                merged = merge_agent_run_override(revision.config, config_override)
                try:
                    validate_agent_config(merged, protocol_policy=self._protocol_policy)
                except AgentConfigValidationError as error:
                    raise agent_revision_not_executable(error.reason) from error
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.models_read,
                )
                # Explicit Skill overrides resolve active keys even when their values match the Revision.
                skills_overridden = config_override is not None and "skills" in config_override.model_fields_set
                skills = await prepare_skills(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    selections=merged.skills,
                    retained=None if skills_overridden else revision.resolved_skills,
                )
            async with short_session(self._sessions) as session:
                subagents = await self._prepare_subagents(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    revision=revision,
                    config=merged,
                )
            try:
                model = await self._model_selector.prepare(
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    model_id=(
                        revision.resolved_model.model_id
                        if merged.model.model_key == revision.config.model.model_key
                        else None
                    ),
                    model_key=(
                        merged.model.model_key if merged.model.model_key != revision.config.model.model_key else None
                    ),
                    settings=merged.model.settings,
                    settings_override=merged.model_settings_override,
                )
                reviewer_model = (
                    await self._model_selector.prepare(
                        organization_id=authorized.organization_id,
                        workspace_id=workspace_id,
                        model_id=merged.reviewer.model,
                        settings=merged.reviewer.model_settings or {},
                    )
                    if merged.reviewer is not None
                    else None
                )
            except ModelError as error:
                raise map_model_error(error) from error
            connectivity = await prepare_invocation_connectivity(
                self._connectivity_resolver,
                actor=actor,
                organization_id=authorized.organization_id,
                workspace_id=workspace_id,
                config=merged,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error
        children = tuple(
            [
                PreparedChildInvocation(
                    edge=edge,
                    invocation=await self.prepare(
                        actor=actor,
                        agent_id=edge.child_agent_id,
                        agent_revision_id=edge.child_agent_revision_id,
                        _active_agents=(*_active_agents, agent_id),
                        _budget=budget,
                    ),
                )
                for edge in subagents
            ]
        )
        return PreparedAgentInvocation(
            root_state_policy=root_state_policy,
            actor=actor,
            organization_id=authorized.organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            agent_revision_id=revision.id,
            selector_kind=selector_kind,
            expected_current_revision_id=expected_current_revision_id,
            revision_content_digest=revision.content_digest,
            revision=revision,
            merged=merged,
            model=model,
            skills=skills,
            subagents=children,
            connectivity=connectivity,
            reviewer_model=reviewer_model,
        )

    async def _prepare_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        revision: AgentRevision,
        config,
    ) -> tuple[ResolvedSubagentEdge, ...]:
        base_edges = {item.name: item for item in revision.resolved_subagents}
        result: list[ResolvedSubagentEdge] = []
        for name, selection in config.subagents.items():
            base_selection = revision.config.subagents.get(name)
            base_edge = base_edges.get(name)
            exact_revision_id = (
                base_edge.child_agent_revision_id if base_selection == selection and base_edge is not None else None
            )
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                action=WorkspaceAction.agent_invoke,
            )
            child = await load_agent_record(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                for_update=False,
            )
            require_invocable_agent(child)
            if exact_revision_id is None:
                exact_revision_id = await select_child_revision_id(
                    session,
                    child=child,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    version=selection.version,
                )
            result.append(
                ResolvedSubagentEdge(
                    name=name,
                    child_agent_id=selection.agent_id,
                    child_agent_revision_id=exact_revision_id,
                    description=selection.description,
                    context=selection.context,
                    usage_limits=selection.usage_limits,
                    environment=selection.environment,
                )
            )
        return tuple(result)
