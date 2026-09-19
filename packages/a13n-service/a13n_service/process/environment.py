"""Environment provider composition shared by Control and Worker roles."""

from __future__ import annotations

from a13n_environment import EnvironmentProviderCatalog, build_environment_provider_catalog
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY

from a13n_service.process.components import Components
from a13n_service.provider_plugins import ProviderCatalogs, load_provider_catalogs
from a13n_service.settings import RedisBackend, Settings


def build_environment_catalog(
    settings: Settings,
    components: Components,
    providers: ProviderCatalogs | None = None,
) -> EnvironmentProviderCatalog:
    """Construct the one immutable Environment catalog shared by owning roles."""

    if settings.environments.local_providers and components.request_authenticator is not None:
        raise ValueError("Deployment local Providers require the OSS identity runtime")
    builtin_keys = settings.environments.provider_builtins
    if (
        settings.redis.backend == RedisBackend.memory
        and "provider_builtins" not in settings.environments.model_fields_set
    ):
        # The supported single-process memory profile has no distributed Stream
        # relay. Explicitly enabling WebSocket still fails startup validation.
        builtin_keys = tuple(key for key in builtin_keys if key != WEBSOCKET_PROVIDER_KEY)
    selected_providers = providers or load_provider_catalogs(())
    selected = components.environment_provider_catalog or build_environment_provider_catalog(
        builtin_keys=(*builtin_keys, *settings.environments.local_providers),
        explicit_providers=(
            provider
            for provider in selected_providers.environment
            if provider.key not in builtin_keys and provider.key not in settings.environments.local_providers
        ),
    )
    if "a13n.local-envd" in selected:
        raise ValueError("Local Envd is not supported by Service")
    if settings.deployment.mode == "distributed" and any(key in selected for key in ("direct-local", "docker")):
        raise ValueError("Local Environment Providers require deployment.mode=single_host")
    return selected


__all__ = ["build_environment_catalog"]
