"""Environment provider composition shared by Control and Worker roles."""

from __future__ import annotations

from a13n_environment import EnvironmentProviderCatalog, build_environment_provider_catalog

from a13n_service.process.components import Components
from a13n_service.settings import Settings


def build_environment_catalog(
    settings: Settings,
    components: Components,
) -> EnvironmentProviderCatalog:
    """Construct the one immutable Environment catalog shared by owning roles."""

    if settings.environments.local_providers and components.request_authenticator is not None:
        raise ValueError("Deployment local Providers require the OSS identity runtime")
    if components.environment_provider_catalog is not None:
        return components.environment_provider_catalog
    selected = build_environment_provider_catalog(
        builtin_keys=(*settings.environments.provider_builtins, *settings.environments.local_providers),
        extension_keys=settings.environments.provider_extensions,
    )
    return selected


__all__ = ["build_environment_catalog"]
