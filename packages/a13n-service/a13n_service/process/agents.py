"""The same Agent admission dependencies for Gateway and Connectivity roles."""

from dataclasses import dataclass

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime


@dataclass(frozen=True, slots=True)
class AgentResources:
    models: AcceptedModelSelector
    plugins: HarnessPluginFactoryCatalog
    connectivity: ConnectivitySelectionResolver
    invocations: AgentInvocationResolver


def build_agent_resources(
    components: Components, shared: SharedRuntime, providers: ProviderRegistry, catalog: HarnessPluginFactoryCatalog
) -> AgentResources:
    sessions = shared.storage.sessions
    models = AcceptedModelSelector(sessions, providers)
    connectivity = ConnectivitySelectionResolver(sessions)
    invocations = components.agent_invocation_resolver or AgentInvocationResolver(
        sessions,
        models,
        plugin_catalog=catalog,
        connectivity_resolver=connectivity,
    )
    return AgentResources(models, catalog, connectivity, invocations)
