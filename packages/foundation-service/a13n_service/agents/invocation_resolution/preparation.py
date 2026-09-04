"""Transaction-free preparation for Agent invocation resolution."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionResolver,
)
from a13n_service.environments.errors import EnvironmentManagementError
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
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedSubagentEdge,
)
from ..environment_resolution import AgentEnvironmentSelectionResolver
from ..errors import (
    agent_current_revision_missing,
    agent_revision_not_executable,
    current_revision_conflict,
    map_authorization_error,
    model_error_reason,
)
from ..invocation import merge_agent_run_override
from ..plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError
from ..resolution import MAX_SUBAGENT_NODES
from ..validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config
from .contracts import (
    AgentSelectorKind,
    PreparedAgentInvocation,
    PreparedAgentRevisionGraph,
    PreparedInvocationSubagent,
    RootAgentStatePolicy,
)
from .environment import require_writable_skill_environment, validate_child_environment
from .graph import (
    load_agent_record,
    load_revision_record,
    require_invocable_agent,
    select_child_revision_id,
    validate_subagent_graph,
)
from .skills import prepare_skills


class AgentInvocationPreparer:
    """Resolve mutable inputs without holding a database transaction across I/O."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        plugin_runtime_mode: PluginRuntimeMode,
        environment_resolver: AgentEnvironmentSelectionResolver | None,
        plugin_resolver: AgentPluginSelectionResolver,
        connectivity_resolver: ConnectivitySelectionResolver | None,
        protocol_policy: AgentProtocolPolicy,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._environment_resolver = environment_resolver
        self._plugin_runtime_mode = plugin_runtime_mode
        self._plugin_resolver = plugin_resolver
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
        run_id: str | None = None,
        _root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
    ) -> PreparedAgentInvocation:
        workspace_id = actor.boundary_workspace_id
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
                require_invocable_agent(agent, policy=_root_state_policy)
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
                if revision.plugin_runtime_mode is not self._plugin_runtime_mode:
                    raise agent_revision_not_executable("plugin_runtime_mode_mismatch")
                merged = merge_agent_run_override(revision.config, config_override)
                try:
                    validate_agent_config(merged.config, protocol_policy=self._protocol_policy)
                except AgentConfigValidationError as error:
                    raise agent_revision_not_executable(error.reason) from error
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.models_read,
                )
                skills = await prepare_skills(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    selections=merged.config.skills,
                    retained=(revision.resolved_skills if merged.config.skills == revision.config.skills else None),
                )
                if merged.config.plugins == revision.config.plugins:
                    try:
                        plugins = await self._plugin_resolver.prepare_retained(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            selections=merged.config.plugins,
                            resolved=revision.resolved_plugin_versions,
                            runtime_lock_digest=revision.runtime_lock_digest,
                        )
                    except PluginSelectionError as error:
                        raise agent_revision_not_executable(error.reason) from error
                else:
                    try:
                        plugins = await self._plugin_resolver.prepare(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            selections=merged.config.plugins,
                        )
                    except PluginSelectionError as error:
                        raise agent_revision_not_executable(error.reason) from error
                resolved_plugins = plugins.resolved
            environment = None
            resolved_environment = None
            if merged.config.environment is not None:
                if self._environment_resolver is None:
                    raise agent_revision_not_executable("environment_resolution_unavailable")
                retained_environment = (
                    revision.resolved_environment if merged.config.environment == revision.config.environment else None
                )
                try:
                    environment = await self._environment_resolver.prepare_invocation(
                        actor=actor,
                        organization_id=authorized.organization_id,
                        workspace_id=workspace_id,
                        selection=merged.config.environment,
                        retained=retained_environment,
                    )
                except EnvironmentManagementError as error:
                    raise agent_revision_not_executable(error.code) from error
                resolved_environment = environment.resolved
            require_writable_skill_environment(merged.config.skills, resolved_environment)
            async with short_session(self._sessions) as session:
                subagents = await self._prepare_subagents(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    root_agent_id=agent_id,
                    revision=revision,
                    config=merged.config,
                    resolved_environment=resolved_environment,
                )
            try:
                model = await self._model_selector.prepare(
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    model_id=(
                        revision.resolved_model.model_id
                        if merged.config.model.model_key == revision.config.model.model_key
                        else None
                    ),
                    model_key=(
                        merged.config.model.model_key
                        if merged.config.model.model_key != revision.config.model.model_key
                        else None
                    ),
                    model_api=merged.config.model.model_api,
                )
            except ModelError as error:
                raise agent_revision_not_executable(model_error_reason(error)) from error
            connectivity = await prepare_invocation_connectivity(
                self._connectivity_resolver,
                actor=actor,
                organization_id=authorized.organization_id,
                workspace_id=workspace_id,
                run_id=run_id,
                config=merged.config,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error
        return PreparedAgentInvocation(
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
            plugins=plugins,
            skills=skills,
            resolved_plugin_versions=resolved_plugins,
            environment=environment,
            resolved_environment=resolved_environment,
            subagents=subagents,
            connectivity=connectivity,
        )

    async def prepare_retained_revision_graph(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        agent_revision_id: str | None = None,
        root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
    ) -> PreparedAgentRevisionGraph:
        """Preflight one retained root Revision and its complete exact child graph."""

        root = await self.prepare(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            _root_state_policy=root_state_policy,
        )
        invocations = [root]
        visited = {root.agent_revision_id}
        pending = list(root.subagents)
        while pending:
            edge = pending.pop().edge
            if edge.child_agent_revision_id in visited:
                continue
            child = await self.prepare(
                actor=actor,
                agent_id=edge.child_agent_id,
                agent_revision_id=edge.child_agent_revision_id,
            )
            visited.add(child.agent_revision_id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise agent_revision_not_executable("subagent_graph_too_large")
            invocations.append(child)
            pending.extend(child.subagents)
        return PreparedAgentRevisionGraph(
            invocations=tuple(invocations),
            root_state_policy=root_state_policy,
        )

    async def _prepare_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        root_agent_id: str,
        revision: AgentRevision,
        config,
        resolved_environment: EnvironmentExecutionConfig | None,
    ) -> tuple[PreparedInvocationSubagent, ...]:
        base_edges = {item.name: item for item in revision.resolved_subagents}
        result: list[PreparedInvocationSubagent] = []
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
            child_revision = await load_revision_record(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=child.id,
                revision_id=exact_revision_id,
                for_update=False,
            )
            if child_revision.plugin_runtime_mode != self._plugin_runtime_mode.value:
                raise agent_revision_not_executable("subagent_runtime_mode_mismatch")
            await validate_subagent_graph(
                session,
                root_agent_id=root_agent_id,
                first_revision=child_revision,
            )
            validate_child_environment(
                resolved_environment,
                child_revision.to_resource().resolved_environment,
                selection,
            )
            result.append(
                PreparedInvocationSubagent(
                    edge=ResolvedSubagentEdge(
                        name=name,
                        child_agent_id=selection.agent_id,
                        child_agent_revision_id=child_revision.id,
                        description=selection.description,
                        context=selection.context,
                        usage_limits=selection.usage_limits,
                        environment=selection.environment,
                    ),
                    child_revision_digest=child_revision.content_digest,
                    child_runtime_lock_digest=child_revision.runtime_lock_digest,
                )
            )
        return tuple(result)
