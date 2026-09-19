"""Service deployment selection of the shared immutable Provider manifests."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.providers.connector import ConnectorProviderDefinition
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.plugins import LoadedProviderPlugin, ProviderManifest, load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition


class ProviderPluginError(RuntimeError):
    """Safe deployment failure before Service readiness."""


@dataclass(frozen=True, slots=True)
class ProviderCatalogs:
    environment: tuple[EnvironmentProviderDefinition, ...]
    model: tuple[ModelProviderDefinition, ...]
    connector: tuple[ConnectorProviderDefinition, ...]
    web: tuple[WebProviderDefinition, ...]
    plugins: tuple[LoadedProviderPlugin, ...]
    memory: tuple[MemoryProviderDefinition, ...] = ()


class _TypedDefinition(Protocol):
    @property
    def type(self) -> str: ...


def _domain[D: _TypedDefinition](
    label: str,
    builtins: Iterable[D],
    plugins: Iterable[LoadedProviderPlugin],
    contribution: Callable[[ProviderManifest], tuple[D, ...]],
) -> tuple[D, ...]:
    """Combine native and installed definitions of one domain without duplicate types."""
    selected = (*builtins, *(item for plugin in plugins for item in contribution(plugin.manifest)))
    if len({definition.type for definition in selected}) != len(selected):
        raise ValueError(f"duplicate {label} Provider type")
    return selected


def load_provider_catalogs(enabled: Iterable[str]) -> ProviderCatalogs:
    try:
        plugins = load_provider_plugins(enabled)
        return ProviderCatalogs(
            environment=_domain("Environment", BUILT_IN_ENVIRONMENT_PROVIDERS, plugins, lambda m: m.environment),
            model=_domain("Model", BUILT_IN_MODEL_PROVIDERS, plugins, lambda m: m.model),
            connector=_domain("Connector", BUILT_IN_CONNECTOR_PROVIDERS, plugins, lambda m: m.connector),
            web=_domain("Web", built_in_web_providers(), plugins, lambda m: m.web),
            memory=_domain("Memory", BUILT_IN_MEMORY_PROVIDERS, plugins, lambda m: m.memory),
            plugins=plugins,
        )
    except (ValueError, TypeError, ImportError) as error:
        raise ProviderPluginError(f"Provider plugin selection failed: {type(error).__name__}") from error
