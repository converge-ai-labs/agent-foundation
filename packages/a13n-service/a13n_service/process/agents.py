"""The same Agent admission dependencies for Gateway, Worker, and Connectivity roles."""

from dataclasses import dataclass

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime


@dataclass(frozen=True, slots=True)
class AgentResources:
    models: AcceptedModelSelector
    connectivity: ConnectivitySelectionResolver
    invocations: AgentInvocationResolver
    web_providers: ProviderCatalog[WebProviderDefinition]
    memory_providers: ProviderCatalog[MemoryProviderDefinition]


def build_agent_resources(
    components: Components,
    shared: SharedRuntime,
    providers: ProviderCatalog[ModelProviderDefinition],
    web_providers: ProviderCatalog[WebProviderDefinition] | None = None,
    memory_providers: ProviderCatalog[MemoryProviderDefinition] | None = None,
) -> AgentResources:
    sessions = shared.storage.sessions
    models = AcceptedModelSelector(sessions, providers)
    connectivity = ConnectivitySelectionResolver(sessions)
    selected_web_providers = web_providers or ProviderCatalog(built_in_web_providers())
    selected_memory = memory_providers if memory_providers is not None else ProviderCatalog()
    invocations = components.agent_invocation_resolver or AgentInvocationResolver(
        sessions,
        models,
        connectivity_resolver=connectivity,
        web_provider_catalog=selected_web_providers,
        memory_provider_catalog=selected_memory,
    )
    return AgentResources(models, connectivity, invocations, selected_web_providers, selected_memory)


def build_agent_resolver(components: Components, shared: SharedRuntime, resources: AgentResources) -> AgentResolver:
    return components.agent_resolver or AgentResolver(
        shared.storage.sessions,
        resources.models,
        connectivity_resolver=resources.connectivity,
        web_provider_catalog=resources.web_providers,
        memory_provider_catalog=resources.memory_providers,
    )
