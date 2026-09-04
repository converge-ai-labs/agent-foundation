"""Trusted distribution component overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.ingress.admission_domain import InputAcceptor
from a13n_service.connectivity.providers import built_in_ingress_adapter_registry
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.keepalive import EnvironmentKeepaliveSourceResolver
from a13n_service.environments.testing import EnvironmentAttachmentTester
from a13n_service.iam import RequestAuthenticator
from a13n_service.models.service import ModelConnectionTester
from a13n_service.plugins.builtins import BuiltinPluginArtifact
from a13n_service.plugins.commands import (
    PluginRuntimeCandidateResolver,
    PluginRuntimeCommandDispatcher,
    PluginRuntimeStagingAuthority,
)
from a13n_service.process.roles import owns_connectivity_data, owns_control, owns_worker
from a13n_service.settings import Settings
from a13n_service.skills.github import GitHubSkillAcquirer
from a13n_service.skills.sources import GitHubCredentialResolver
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.service import TraceAccessAuthorizer


@dataclass(frozen=True, slots=True)
class Components:
    """Narrow component overrides supplied by the selected distribution."""

    request_authenticator: RequestAuthenticator | None = None
    agent_resolver: AgentResolver | None = None
    agent_invocation_resolver: AgentInvocationResolver | None = None
    agent_plugin_selection_resolver: AgentPluginSelectionResolver | None = None
    plugin_runtime_command_dispatcher: PluginRuntimeCommandDispatcher | None = None
    plugin_runtime_candidate_resolver: PluginRuntimeCandidateResolver | None = None
    plugin_runtime_staging_authority: PluginRuntimeStagingAuthority | None = None
    model_connection_tester: ModelConnectionTester | None = None
    environment_provider_catalog: FoundationEnvironmentProviderCatalog | None = None
    environment_attachment_tester: EnvironmentAttachmentTester | None = None
    environment_keepalive_source_resolver: EnvironmentKeepaliveSourceResolver | None = None
    skill_github_acquirer: GitHubSkillAcquirer | None = None
    skill_credential_resolver: GitHubCredentialResolver | None = None
    trace_access_authorizer: TraceAccessAuthorizer | None = None
    trace_query_provider_registry: TraceQueryProviderRegistry | None = None
    ingress_adapter_registry: AdapterRegistry[IngressAdapter] | None = None
    connector_provider_registry: ConnectorProviderRegistry | None = None
    input_acceptor: InputAcceptor | None = None
    builtin_plugin_artifacts: tuple[BuiltinPluginArtifact, ...] = ()


def snapshot_components(settings: Settings, components: Components) -> Components:
    """Freeze mutable distribution registries at application construction."""

    ingress_adapters = None
    if owns_control(settings.role) or owns_connectivity_data(settings.role):
        ingress_adapters = (
            components.ingress_adapter_registry
            or built_in_ingress_adapter_registry(allowed_provider_origins=settings.connectivity_provider_origins)
        ).copy()
    connector_providers = components.connector_provider_registry
    if not (owns_control(settings.role) or owns_worker(settings.role)) or connector_providers is None:
        connector_providers = None
    else:
        connector_providers = connector_providers.copy()
    return replace(
        components,
        ingress_adapter_registry=ingress_adapters,
        connector_provider_registry=connector_providers,
    )


__all__ = ["Components", "snapshot_components"]
