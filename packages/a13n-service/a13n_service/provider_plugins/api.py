"""Small typed registration surface supported for external Provider packages."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from a13n_environment import EnvironmentProvider
from a13n_harness.capabilities.web import (
    WebPolicy,
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.memory_plugins import MemoryBackendPlugin
from pydantic import BaseModel

from a13n_service.connectivity.connectors.contracts import ConnectorProviderRuntime
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.domain import JsonObject
from a13n_service.models.provider_adapters.base import ProviderIntegration

PROVIDER_EXTENSION_API_VERSION = 1


class WebProviderResponseError(WebProviderError):
    """Safe explicit provider response that can be eligible for bounded retry."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        if retry_after is not None and (
            type(retry_after) not in {int, float} or math.isnan(retry_after) or retry_after < 0
        ):
            raise ValueError("retry_after must be a non-negative number")
        super().__init__(code)
        self.retry_after = retry_after


class WebProviderRuntime(Protocol):
    """One operation-scoped Web adapter constructed by a registered factory."""

    async def search(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebSearchRequest,
        max_results: int,
        allow_domains: tuple[str, ...],
        deny_domains: tuple[str, ...],
    ) -> WebSearchResponse: ...

    async def scrape(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebScrapeRequest,
        policy: WebPolicy,
        max_content_bytes: int,
    ) -> WebScrapeResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WebProviderRegistration:
    type: str
    display_name: str
    configuration_model: type[BaseModel]
    credential_model: type[BaseModel]
    setup_url: str
    factory: Callable[[], WebProviderRuntime]
    supports_search: bool = False
    supports_scrape: bool = False
    supports_restricted_scrape: bool = False


ConnectorSetupValidator = Callable[[JsonObject, str, object], JsonObject]
ConnectorRuntimeFactory = Callable[[ConnectorHttpClient, JsonObject, JsonObject], ConnectorProviderRuntime]


@dataclass(frozen=True, slots=True)
class ConnectorProviderRegistration:
    type: str
    display_name: str
    configuration_model: type[BaseModel]
    credential_model: type[BaseModel]
    setup_validator: ConnectorSetupValidator
    factory: ConnectorRuntimeFactory


class _DomainRegistry[T]:
    def __init__(self, label: str, value_type: type[T], key: Callable[[T], str]) -> None:
        self._label = label
        self._value_type = value_type
        self._key = key
        self._values: dict[str, T] = {}

    def register(self, value: T) -> None:
        if not isinstance(value, self._value_type):
            raise TypeError(f"{self._label} Provider registration has an invalid type")
        key = self._key(value)
        if key in self._values:
            raise ValueError(f"duplicate {self._label} Provider type {key!r}")
        self._values[key] = value

    def values(self) -> tuple[T, ...]:
        return tuple(self._values[key] for key in sorted(self._values))


class ProviderPluginRegistry:
    """Mutable only while one selected entry point registers inert definitions."""

    def __init__(self, *, api_version: int) -> None:
        if api_version != PROVIDER_EXTENSION_API_VERSION:
            raise ValueError(
                f"unsupported Provider extension API version {api_version}; expected {PROVIDER_EXTENSION_API_VERSION}"
            )
        self.environment = _DomainRegistry[EnvironmentProvider](
            "Environment", EnvironmentProvider, lambda item: item.key
        )
        self.model = _DomainRegistry[ProviderIntegration]("Model", ProviderIntegration, lambda item: item.type)
        self.connector = _DomainRegistry[ConnectorProviderRegistration](
            "Connector", ConnectorProviderRegistration, lambda item: item.type
        )
        self.web = _DomainRegistry[WebProviderRegistration]("Web", WebProviderRegistration, lambda item: item.type)
        self.memory = _DomainRegistry[MemoryBackendPlugin[Any, Any]](
            "Memory", MemoryBackendPlugin, lambda item: item.key
        )


class ProviderRegister(Protocol):
    a13n_provider_api_version: int

    def __call__(self, registry: ProviderPluginRegistry) -> None: ...


def provider_plugin(*, api_version: int) -> Callable[[Callable[[ProviderPluginRegistry], None]], ProviderRegister]:
    """Declare the exact extension API version implemented by one entry point."""

    def declare(register: Callable[[ProviderPluginRegistry], None]) -> ProviderRegister:
        versioned = cast(ProviderRegister, register)
        versioned.a13n_provider_api_version = api_version
        return versioned

    return declare
