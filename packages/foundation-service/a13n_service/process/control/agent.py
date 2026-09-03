"""Agent control-plane construction."""

from __future__ import annotations

from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.agents.service import AgentService
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.process.components import ServiceComponents
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import ServiceSettings


def build_agent_service(
    settings: ServiceSettings,
    components: ServiceComponents,
    shared: SharedRuntime,
    accepted_models: AcceptedModelSelector,
    environment_selection: AgentEnvironmentSelectionResolver,
    plugin_selection: AgentPluginSelectionResolver,
    connectivity_selection: ConnectivitySelectionResolver | None,
) -> AgentService:
    """Construct Agent read and invocation resolution behind one service."""

    resolver = components.agent_resolver or AgentResolver(
        shared.storage.sessions,
        accepted_models,
        plugin_runtime_mode=settings.plugin_runtime_mode,
        environment_resolver=environment_selection,
        plugin_resolver=plugin_selection,
        connectivity_resolver=connectivity_selection,
    )
    invocation_resolver = components.agent_invocation_resolver or AgentInvocationResolver(
        shared.storage.sessions,
        accepted_models,
        plugin_runtime_mode=settings.plugin_runtime_mode,
        environment_resolver=environment_selection,
        plugin_resolver=plugin_selection,
        connectivity_resolver=connectivity_selection,
    )
    return AgentService(shared.storage.sessions, resolver, invocation_resolver)


__all__ = ["build_agent_service"]
