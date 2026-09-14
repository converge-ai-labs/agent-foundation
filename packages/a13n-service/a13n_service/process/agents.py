"""The same Agent admission dependencies for Gateway, Worker, and Connectivity roles."""

from dataclasses import dataclass

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime
from a13n_service.web.registry import WebProviderRegistry, built_in_web_provider_registry


@dataclass(frozen=True, slots=True)
class AgentResources:
    models: AcceptedModelSelector
    connectivity: ConnectivitySelectionResolver
    invocations: AgentInvocationResolver
    web_providers: WebProviderRegistry


def build_agent_resources(
    components: Components,
    shared: SharedRuntime,
    providers: ProviderRegistry,
    web_providers: WebProviderRegistry | None = None,
) -> AgentResources:
    sessions = shared.storage.sessions
    models = AcceptedModelSelector(sessions, providers)
    connectivity = ConnectivitySelectionResolver(sessions)
    selected_web_providers = web_providers or built_in_web_provider_registry()
    invocations = components.agent_invocation_resolver or AgentInvocationResolver(
        sessions,
        models,
        connectivity_resolver=connectivity,
        web_provider_registry=selected_web_providers,
    )
    return AgentResources(models, connectivity, invocations, selected_web_providers)
