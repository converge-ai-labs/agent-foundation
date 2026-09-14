"""Immutable Web Provider definitions and operation-scoped runtime dispatch."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from anyio import move_on_after
from pydantic import BaseModel, SecretBytes, SecretStr, TypeAdapter

from a13n_service.provider_plugins.api import WebProviderRegistration, WebProviderRuntime

from .domain import WebProviderDefinition

if TYPE_CHECKING:
    from .adapters import WebProviderTransport

logger = logging.getLogger("a13n_service.web.registry")
_RUNTIME_CLEANUP_TIMEOUT_SECONDS = 1
_JSON_SERIALIZER = TypeAdapter(Any)


class WebProviderRegistry:
    def __init__(self, registrations: Iterable[WebProviderRegistration]) -> None:
        indexed: dict[str, WebProviderRegistration] = {}
        for registration in registrations:
            if registration.type in indexed:
                raise ValueError(f"duplicate Web Provider type {registration.type!r}")
            indexed[registration.type] = registration
        self._registrations = MappingProxyType(indexed)

    def require(self, provider_type: str) -> WebProviderRegistration:
        try:
            return self._registrations[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown Web Provider type {provider_type!r}") from error

    def definitions(self) -> tuple[WebProviderDefinition, ...]:
        return tuple(self._definition(self._registrations[key]) for key in sorted(self._registrations))

    def validate_configuration(self, provider_type: str, value: object) -> dict[str, object]:
        return self._validated_json(self.require(provider_type).configuration_model, value)

    def validate_credentials(self, provider_type: str, value: object) -> BaseModel:
        return self.require(provider_type).credential_model.model_validate(value)

    def credential_payload(self, provider_type: str, value: object) -> dict[str, object]:
        """Validate credentials and reveal secret fields only for encrypted persistence."""

        credentials = self.validate_credentials(provider_type, value)
        revealed = _reveal_secrets(credentials.model_dump(mode="python", by_alias=True))
        payload = _JSON_SERIALIZER.dump_python(revealed, mode="json")
        if not isinstance(payload, dict):
            raise TypeError("Web Provider credentials must serialize as an object")
        return payload

    @asynccontextmanager
    async def runtime(self, provider_type: str) -> AsyncIterator[WebProviderRuntime]:
        runtime = self.require(provider_type).factory()
        try:
            yield runtime
        finally:
            try:
                with move_on_after(_RUNTIME_CLEANUP_TIMEOUT_SECONDS, shield=True) as cleanup_scope:
                    await runtime.aclose()
            except Exception as error:
                logger.warning(
                    "Web Provider runtime cleanup failed",
                    extra={"provider_type": provider_type, "cleanup_error_type": type(error).__name__},
                )
            else:
                if cleanup_scope.cancel_called:
                    logger.warning("Web Provider runtime cleanup timed out", extra={"provider_type": provider_type})

    @staticmethod
    def _validated_json(model: type[BaseModel], value: object) -> dict[str, object]:
        parsed = model.model_validate(value)
        return parsed.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _definition(registration: WebProviderRegistration) -> WebProviderDefinition:
        operations: list[Literal["search", "scrape"]] = []
        if registration.supports_search:
            operations.append("search")
        if registration.supports_scrape:
            operations.append("scrape")
        credential_schema = registration.credential_model.model_json_schema()
        credential_schema["writeOnly"] = True
        return WebProviderDefinition(
            type=registration.type,
            display_name=registration.display_name,
            configuration_schema=registration.configuration_model.model_json_schema(),
            credential_schema=credential_schema,
            setup_url=registration.setup_url,
            operations=tuple(operations),
            supports_restricted_scrape=registration.supports_restricted_scrape,
        )


def built_in_web_provider_registry(*, transport: WebProviderTransport | None = None) -> WebProviderRegistry:
    """Build the canonical built-in snapshot, optionally with a test transport."""

    from a13n_service.provider_plugins import load_provider_catalogs
    from a13n_service.web.adapters import BoundWebProviderRuntime, WebProviderTransport

    registrations = load_provider_catalogs(()).web
    if transport is not None:
        if not isinstance(transport, WebProviderTransport):
            raise TypeError("transport must be a WebProviderTransport")
        selected_transport = transport
        registrations = tuple(
            replace(
                registration,
                factory=lambda registration=registration: BoundWebProviderRuntime(
                    registration.type, selected_transport
                ),
            )
            for registration in registrations
        )
    return WebProviderRegistry(registrations)


def _reveal_secrets(value: object) -> object:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, SecretBytes):
        return value.get_secret_value().decode("utf-8")
    if isinstance(value, dict):
        return {key: _reveal_secrets(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_reveal_secrets(item) for item in value]
    return value


__all__ = ["WebProviderRegistry", "built_in_web_provider_registry"]
