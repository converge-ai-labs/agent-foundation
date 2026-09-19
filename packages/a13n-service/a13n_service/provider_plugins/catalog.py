"""Service deployment selection of the shared immutable Provider manifests."""

from collections.abc import Iterable
from dataclasses import dataclass

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.plugins import LoadedProviderPlugin, load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition


class ProviderPluginError(RuntimeError):
    """Safe deployment failure before Service readiness."""


@dataclass(frozen=True, slots=True)
class ProviderCatalogs:
    """One catalog per domain, combining native definitions with the selected plugins."""

    environment: ProviderCatalog[EnvironmentProviderDefinition]
    model: ProviderCatalog[ModelProviderDefinition]
    connector: ProviderCatalog[ConnectorProviderDefinition]
    web: ProviderCatalog[WebProviderDefinition]
    memory: ProviderCatalog[MemoryProviderDefinition]
    plugins: tuple[LoadedProviderPlugin, ...] = ()


def load_provider_catalogs(enabled: Iterable[str]) -> ProviderCatalogs:
    """Each catalog rejects a duplicate type; failure stays safe and precedes readiness."""
    try:
        plugins = load_provider_plugins(enabled)
        manifests = tuple(plugin.manifest for plugin in plugins)
        return ProviderCatalogs(
            environment=ProviderCatalog(
                (*BUILT_IN_ENVIRONMENT_PROVIDERS, *(item for m in manifests for item in m.environment))
            ),
            model=ProviderCatalog((*BUILT_IN_MODEL_PROVIDERS, *(item for m in manifests for item in m.model))),
            connector=ProviderCatalog(
                (*BUILT_IN_CONNECTOR_PROVIDERS, *(item for m in manifests for item in m.connector))
            ),
            web=ProviderCatalog((*built_in_web_providers(), *(item for m in manifests for item in m.web))),
            memory=ProviderCatalog((*BUILT_IN_MEMORY_PROVIDERS, *(item for m in manifests for item in m.memory))),
            plugins=plugins,
        )
    except (ValueError, TypeError, ImportError) as error:
        raise ProviderPluginError(f"Provider plugin selection failed: {type(error).__name__}") from error
