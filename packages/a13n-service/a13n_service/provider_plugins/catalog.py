"""Selected entry-point loading and immutable Provider catalog snapshots."""

from __future__ import annotations

import importlib.metadata
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from a13n_environment import EnvironmentProvider
from a13n_harness.memory_plugins import MemoryBackendCatalog, MemoryBackendPlugin
from pydantic import BaseModel

from a13n_service.models.provider_adapters.base import ProviderIntegration

from .api import (
    PROVIDER_EXTENSION_API_VERSION,
    ConnectorProviderRegistration,
    ProviderPluginRegistry,
    WebProviderRegistration,
)

ENTRY_POINT_GROUP = "a13n.providers"
_ENTRY_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROVIDER_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENVIRONMENT_BUILTINS = frozenset(
    {
        "a13n.direct-local",
        "a13n.docker",
        "a13n.e2b",
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
    model: tuple[ProviderIntegration, ...]
    connector: tuple[ConnectorProviderRegistration, ...]
    web: tuple[WebProviderRegistration, ...]
    plugins: tuple[LoadedProviderPlugin, ...]
    memory: tuple[MemoryBackendPlugin[Any, Any], ...] = ()


def load_provider_catalogs(enabled: Iterable[str]) -> ProviderCatalogs:
    """Register built-ins plus only explicitly selected installed entry points."""

    selected = tuple(enabled)
    if len(set(selected)) != len(selected):
        raise ProviderPluginError("provider_plugins.enabled contains a duplicate entry-point name")
    for name in selected:
        if _ENTRY_NAME.fullmatch(name) is None:
            raise ProviderPluginError(f"invalid Provider plugin entry-point name {name!r}")

    registry = ProviderPluginRegistry(api_version=PROVIDER_EXTENSION_API_VERSION)
    from .builtins import register as register_builtins

    register_builtins(registry)
    loaded: list[LoadedProviderPlugin] = []
    available: dict[str, list[importlib.metadata.EntryPoint]] = {}
    if selected:
        try:
            for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
                if entry_point.name in selected:
                    available.setdefault(entry_point.name, []).append(entry_point)
        except Exception as error:
            raise ProviderPluginError("Provider plugin metadata could not be enumerated") from error

    for name in selected:
        matches = available.get(name, [])
        if not matches:
            raise ProviderPluginError(f"selected Provider plugin {name!r} is not installed")
        if len(matches) != 1:
            raise ProviderPluginError(f"selected Provider plugin {name!r} is ambiguous")
        entry_point = matches[0]
        distribution = entry_point.dist
        distribution_name = _metadata_text(distribution, "Name")
        distribution_version = "unknown" if distribution is None else distribution.version
        try:
            register = entry_point.load()
            if not isinstance(register, Callable):
                raise TypeError("entry point is not callable")
            api_version = getattr(register, "a13n_provider_api_version", None)
            if api_version != PROVIDER_EXTENSION_API_VERSION:
                raise TypeError(
                    f"unsupported extension API version {api_version!r}; expected {PROVIDER_EXTENSION_API_VERSION}"
                )
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

    _validate(registry)
    return ProviderCatalogs(
        environment=registry.environment.values(),
        model=registry.model.values(),
        connector=registry.connector.values(),
        web=registry.web.values(),
        plugins=tuple(loaded),
        memory=registry.memory.values(),
    )


def _metadata_text(distribution: importlib.metadata.Distribution | None, key: str) -> str:
    if distribution is None:
        return "unknown"
    value = distribution.metadata.get(key)
    return value if isinstance(value, str) and value else "unknown"


def _validate(registry: ProviderPluginRegistry) -> None:
    MemoryBackendCatalog(registry.memory.values())
    for plugin in registry.memory.values():
        _validate_display_name("Memory", plugin.key, plugin.display_name)
        _validate_schema("Memory", plugin.key, plugin.configuration_model)
        _validate_schema("Memory credential", plugin.key, plugin.credential_model)
    for provider in registry.environment.values():
        if provider.key in _ENVIRONMENT_BUILTINS:
            raise ProviderPluginError(f"Environment Provider {provider.key!r} uses a reserved built-in key")
    for registration in registry.web.values():
        _validate_type("Web", registration.type)
        _validate_display_name("Web", registration.type, registration.display_name)
        _validate_setup_url(registration.type, registration.setup_url)
        if not callable(registration.factory):
            raise ProviderPluginError(f"Web Provider {registration.type!r} has an invalid factory")
        if not registration.supports_search and not registration.supports_scrape:
            raise ProviderPluginError(f"Web Provider {registration.type!r} has no supported operation")
        if registration.supports_restricted_scrape and not registration.supports_scrape:
            raise ProviderPluginError(f"Web Provider {registration.type!r} advertises restricted scrape without scrape")
        _validate_schema("Web", registration.type, registration.configuration_model)
        _validate_schema("Web credential", registration.type, registration.credential_model)
    for registration in registry.connector.values():
        _validate_type("Connector", registration.type)
        _validate_display_name("Connector", registration.type, registration.display_name)
        if not callable(registration.setup_validator) or not callable(registration.factory):
            raise ProviderPluginError(f"Connector Provider {registration.type!r} has an invalid runtime hook")
        _validate_schema("Connector", registration.type, registration.configuration_model)
        _validate_schema("Connector credential", registration.type, registration.credential_model)
    # The existing constructors own deeper domain validation and produce clearer errors.
    from a13n_service.models.providers import ProviderRegistry

    for integration in registry.model.values():
        _validate_type("Model", integration.type)
        _validate_schema("Model", integration.type, integration.configuration_model)
    ProviderRegistry(registry.model.values())


def _validate_type(label: str, provider_type: str) -> None:
    if _PROVIDER_TYPE.fullmatch(provider_type) is None:
        raise ProviderPluginError(f"{label} Provider type {provider_type!r} is invalid")


def _validate_schema(label: str, provider_type: str, model: object) -> None:
    try:
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("not a Pydantic model")
        schema = model.model_json_schema()
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise TypeError("schema must describe an object")
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ValueError("schema is too large")
        pending: list[object] = [schema]
        visited = 0
        while pending:
            value = pending.pop()
            visited += 1
            if visited > 10_000:
                raise ValueError("schema is too complex")
            if isinstance(value, dict):
                reference = value.get("$ref")
                if reference is not None and (not isinstance(reference, str) or not reference.startswith("#/$defs/")):
                    raise ValueError("schema contains a remote reference")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except Exception as error:
        raise ProviderPluginError(f"{label} Provider {provider_type!r} has an invalid schema") from error


def _validate_display_name(label: str, provider_type: str, display_name: str) -> None:
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 128:
        raise ProviderPluginError(f"{label} Provider {provider_type!r} has an invalid display name")


def _validate_setup_url(provider_type: str, setup_url: str) -> None:
    parsed = urlsplit(setup_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ProviderPluginError(f"Web Provider {provider_type!r} has an invalid setup URL")
