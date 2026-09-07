"""The same Agent admission dependencies for Gateway and Connectivity roles."""

from dataclasses import dataclass

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


@dataclass(frozen=True, slots=True)
class AgentResources:
    models: AcceptedModelSelector
    plugins: AgentPluginSelectionResolver
    connectivity: ConnectivitySelectionResolver
    invocations: AgentInvocationResolver


def build_agent_resources(
    settings: Settings, components: Components, shared: SharedRuntime, providers: ProviderRegistry
) -> AgentResources:
    sessions = shared.storage.sessions
    models = AcceptedModelSelector(sessions, providers)
    plugins = components.agent_plugin_selection_resolver or AgentPluginSelectionResolver(
        sessions, runtime_mode=settings.plugin_runtime_mode, worker_release=settings.build_version
    )
    connectivity = ConnectivitySelectionResolver(sessions)
    invocations = components.agent_invocation_resolver or AgentInvocationResolver(
        sessions,
        models,
        plugin_runtime_mode=settings.plugin_runtime_mode,
        plugin_resolver=plugins,
        connectivity_resolver=connectivity,
    )
    return AgentResources(models, plugins, connectivity, invocations)
