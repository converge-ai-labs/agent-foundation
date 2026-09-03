"""Environment control-plane construction."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_environment_provider import build_environment_provider_catalog

from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.process.components import ServiceComponents
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import ServiceSettings


@dataclass(frozen=True, slots=True)
class _EnvironmentBundle:
    service: EnvironmentManagementService
    agent_selection: AgentEnvironmentSelectionResolver


def build_environment_bundle(
    settings: ServiceSettings,
    components: ServiceComponents,
    shared: SharedRuntime,
) -> _EnvironmentBundle:
    """Construct environment management and Agent selection from one catalog."""

    catalog = _environment_provider_catalog(settings, components)
    return _EnvironmentBundle(
        service=EnvironmentManagementService(
            shared.storage.sessions,
            catalog,
            attachment_tester=components.environment_attachment_tester,
        ),
        agent_selection=AgentEnvironmentSelectionResolver(shared.storage.sessions, catalog),
    )


def _environment_provider_catalog(
    settings: ServiceSettings,
    components: ServiceComponents,
) -> FoundationEnvironmentProviderCatalog:
    if components.environment_provider_catalog is not None:
        return components.environment_provider_catalog
    selected = build_environment_provider_catalog(
        builtin_keys=settings.environment_provider_builtins,
        extension_keys=settings.environment_provider_extensions,
    )
    return FoundationEnvironmentProviderCatalog.from_environment_provider_catalog(selected)


__all__ = ["build_environment_bundle"]
