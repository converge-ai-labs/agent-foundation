"""Environment provider composition shared by Control and Worker roles."""

from __future__ import annotations

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS

from a13n_service.process.components import Components
from a13n_service.provider_plugins import ProviderCatalogs, load_provider_catalogs
from a13n_service.settings import Settings


def build_environment_catalog(
    settings: Settings,
    components: Components,
    providers: ProviderCatalogs | None = None,
) -> ProviderCatalog[EnvironmentProviderDefinition]:
    """Construct the one immutable Environment catalog shared by owning roles."""

    if settings.environments.local_providers and components.request_authenticator is not None:
        raise ValueError("Deployment local Providers require the OSS identity runtime")
    selected_providers = providers or load_provider_catalogs(())
    selected_keys = {*settings.environments.provider_builtins, *settings.environments.local_providers}
    builtin_keys = {definition.type for definition in BUILT_IN_ENVIRONMENT_PROVIDERS}
    selected = components.environment_provider_catalog or ProviderCatalog(
        definition
        for definition in selected_providers.environment.values()
        if definition.type in selected_keys or definition.type not in builtin_keys
    )
    if "local_envd" in selected:
        raise ValueError("Local Envd is not supported by Service")
    if settings.deployment.mode == "distributed" and any(key in selected for key in ("direct_local", "docker")):
        raise ValueError("Local Environment Providers require deployment.mode=single_host")
    return selected


__all__ = ["build_environment_catalog"]
