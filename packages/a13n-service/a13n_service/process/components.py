"""Trusted distribution component overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.memory import MemoryProviderCatalog

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.composition import ConnectorProviders
from a13n_service.connectivity.ingress.admission_domain import InputAcceptor
from a13n_service.connectivity.providers import built_in_ingress_adapter_registry
from a13n_service.iam import RequestAuthenticator
from a13n_service.models.service import ModelConnectionTester
from a13n_service.process.roles import owns_connectivity_data, owns_control, owns_worker
from a13n_service.settings import Settings
from a13n_service.skills.github import GitHubSkillAcquirer
from a13n_service.skills.sources import GitHubCredentialResolver
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.service import TraceAccessAuthorizer

if TYPE_CHECKING:
    from a13n_service.models.catalog import ModelCatalog


@dataclass(frozen=True, slots=True)
class Components:
    """Narrow component overrides supplied by the selected distribution."""

    request_authenticator: RequestAuthenticator | None = None
    agent_resolver: AgentResolver | None = None
    agent_invocation_resolver: AgentInvocationResolver | None = None
    model_connection_tester: ModelConnectionTester | None = None
    model_catalog: ModelCatalog | None = None
    environment_provider_catalog: EnvironmentProviderCatalog | None = None
    memory_provider_catalog: MemoryProviderCatalog | None = None
    skill_github_acquirer: GitHubSkillAcquirer | None = None
    skill_credential_resolver: GitHubCredentialResolver | None = None
    trace_access_authorizer: TraceAccessAuthorizer | None = None
    trace_query_provider_registry: TraceQueryProviderRegistry | None = None
    ingress_adapter_registry: AdapterRegistry[IngressAdapter] | None = None
    connector_providers: ConnectorProviders | None = None
    input_acceptor: InputAcceptor | None = None
    plugin_factory_catalog: HarnessPluginFactoryCatalog | None = None


def snapshot_components(settings: Settings, components: Components) -> Components:
    """Freeze mutable distribution registries at application construction."""

    ingress_adapters = None
    if owns_control(settings.service.role) or owns_connectivity_data(settings.service.role):
        ingress_adapters = (
            components.ingress_adapter_registry
            or built_in_ingress_adapter_registry(allowed_provider_origins=settings.connectivity.provider_origins)
        ).copy()
    connector_providers = components.connector_providers
    if not (owns_control(settings.service.role) or owns_worker(settings.service.role)) or connector_providers is None:
        connector_providers = None
    return replace(
        components,
        ingress_adapter_registry=ingress_adapters,
        connector_providers=connector_providers,
    )


__all__ = ["Components", "snapshot_components"]
