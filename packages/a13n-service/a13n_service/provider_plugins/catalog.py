"""Selected entry-point loading and immutable Provider catalog snapshots."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from a13n_environment import EnvironmentProvider
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.plugins import ProviderManifest, selected_entry_points
from a13n_harness.providers.validation import validate_display_name, validate_schema, validate_type
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition

from .api import (
    PROVIDER_EXTENSION_API_VERSION,
    ConnectorProviderRegistration,
    ProviderPluginRegistry,
    _DomainRegistry,
)

_ENVIRONMENT_BUILTINS = frozenset(
    {
        "direct-local",
        "docker",
        "e2b",
        "daytona",
        "modal",
        "vercel",
        "sprites",
        "runloop",
        "a13n.http-envd",
        "a13n.websocket-envd",
    }
)


class ProviderPluginError(RuntimeError):
    """Safe deployment configuration failure raised before Service readiness."""


@dataclass(frozen=True, slots=True)
class LoadedProviderPlugin:
    entry_point: str
    distribution_name: str
    distribution_version: str
    import_target: str


@dataclass(frozen=True, slots=True)
class ProviderCatalogs:
    environment: tuple[EnvironmentProvider, ...]
    model: tuple[ModelProviderDefinition, ...]
    connector: tuple[ConnectorProviderRegistration, ...]
    web: tuple[WebProviderDefinition, ...]
    plugins: tuple[LoadedProviderPlugin, ...]
    memory: tuple[MemoryProviderDefinition, ...] = ()


def load_provider_catalogs(enabled: Iterable[str]) -> ProviderCatalogs:
    """Register built-ins plus only explicitly selected installed entry points."""

    try:
        selected = selected_entry_points(enabled)
    except Exception as error:
        raise ProviderPluginError(str(error)) from error

    registry = ProviderPluginRegistry(api_version=PROVIDER_EXTENSION_API_VERSION)
    from .builtins import register as register_builtins

    register_builtins(registry)
    model = _DomainRegistry("Model", ModelProviderDefinition, lambda definition: definition.type)
    for definition in BUILT_IN_MODEL_PROVIDERS:
        model.register(definition)
    web = _DomainRegistry("Web", WebProviderDefinition, lambda definition: definition.type)
    for definition in built_in_web_providers():
        web.register(definition)
    memory = _DomainRegistry("Memory", MemoryProviderDefinition, lambda definition: definition.type)
    for definition in BUILT_IN_MEMORY_PROVIDERS:
        memory.register(definition)
    loaded: list[LoadedProviderPlugin] = []
    for entry_point in selected:
        name = entry_point.name
        distribution = entry_point.dist
        distribution_name = _metadata_text(distribution, "Name")
        distribution_version = "unknown" if distribution is None else distribution.version
        try:
            register = entry_point.load()
            if isinstance(register, ProviderManifest):
                for definition in register.memory:
                    memory.register(definition)
                for definition in register.model:
                    model.register(definition)
                for definition in register.web:
                    web.register(definition)
            elif not isinstance(register, Callable):
                raise TypeError("entry point is not callable")
            api_version = (
                register.api_version
                if isinstance(register, ProviderManifest)
                else getattr(register, "a13n_provider_api_version", None)
            )
            if api_version != PROVIDER_EXTENSION_API_VERSION:
                raise TypeError(
                    f"unsupported extension API version {api_version!r}; expected {PROVIDER_EXTENSION_API_VERSION}"
                )
            if not isinstance(register, ProviderManifest):
                register(registry)
        except Exception as error:
            raise ProviderPluginError(
                f"Provider plugin {name!r} from {distribution_name!r} failed registration: {type(error).__name__}"
            ) from error
        loaded.append(
            LoadedProviderPlugin(
                entry_point=name,
                distribution_name=distribution_name,
                distribution_version=distribution_version,
                import_target=entry_point.value,
            )
        )

    try:
        _validate(registry)
    except ValueError as error:
        raise ProviderPluginError(str(error)) from error
    return ProviderCatalogs(
        environment=registry.environment.values(),
        model=model.values(),
        connector=registry.connector.values(),
        web=web.values(),
        plugins=tuple(loaded),
        memory=memory.values(),
    )


def _metadata_text(distribution: importlib.metadata.Distribution | None, key: str) -> str:
    if distribution is None:
        return "unknown"
    value = distribution.metadata.get(key)
    return value if isinstance(value, str) and value else "unknown"


def _validate(registry: ProviderPluginRegistry) -> None:
    for provider in registry.environment.values():
        if provider.key in _ENVIRONMENT_BUILTINS:
            raise ProviderPluginError(f"Environment Provider {provider.key!r} uses a reserved built-in key")
    for registration in registry.connector.values():
        validate_type("Connector", registration.type)
        validate_display_name("Connector", registration.type, registration.display_name)
        if not callable(registration.setup_validator) or not callable(registration.factory):
            raise ProviderPluginError(f"Connector Provider {registration.type!r} has an invalid runtime hook")
        validate_schema("Connector", registration.type, registration.configuration_model)
        validate_schema("Connector credential", registration.type, registration.credential_model)
