"""Service catalog projection and secret-preserving input validation."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Literal

from a13n_harness.providers.web.definition import WebProviderDefinition as Definition
from a13n_harness.providers.web.transport import WebProviderTransport
from pydantic import BaseModel

from a13n_service.credentials import credential_payload

from .domain import WebProviderDefinition


class WebProviderRegistry:
    def __init__(self, definitions: Iterable[Definition], *, transport: WebProviderTransport | None = None) -> None:
        self.transport = transport
        indexed: dict[str, Definition] = {}
        for definition in definitions:
            if definition.type in indexed:
                raise ValueError(f"duplicate Web Provider type {definition.type!r}")
            indexed[definition.type] = definition
        self._definitions = MappingProxyType(indexed)

    def require(self, provider_type: str) -> Definition:
        try:
            return self._definitions[provider_type]
        except KeyError as error:
            raise ValueError(f"unknown Web Provider type {provider_type!r}") from error

    def definitions(self) -> tuple[WebProviderDefinition, ...]:
        return tuple(self._definition(self._definitions[key]) for key in sorted(self._definitions))

    def validate_configuration(self, provider_type: str, value: object) -> dict[str, object]:
        return self._validated_json(self.require(provider_type).configuration_model, value)

    def validate_credentials(self, provider_type: str, value: object) -> BaseModel:
        return self.require(provider_type).credential_model.model_validate(value)

    def credential_payload(self, provider_type: str, value: object) -> dict[str, object]:
        """Validate credentials and reveal secret fields only for encrypted persistence."""

        return credential_payload(self.validate_credentials(provider_type, value))

    @staticmethod
    def _validated_json(model: type[BaseModel], value: object) -> dict[str, object]:
        parsed = model.model_validate(value)
        return parsed.model_dump(mode="json", by_alias=True, exclude_none=False)

    @staticmethod
    def _definition(definition: Definition) -> WebProviderDefinition:
        operations: list[Literal["search", "scrape"]] = []
        if definition.supports_search:
            operations.append("search")
        if definition.supports_scrape:
            operations.append("scrape")
        credential_schema = definition.credential_model.model_json_schema()
        credential_schema["writeOnly"] = True
        return WebProviderDefinition(
            type=definition.type,
            display_name=definition.display_name,
            configuration_schema=definition.configuration_model.model_json_schema(),
            credential_schema=credential_schema,
            credential_required=definition.credential_required,
            setup_url=definition.setup_url,
            operations=tuple(operations),
            supports_restricted_scrape=definition.supports_restricted_scrape,
        )


def built_in_web_provider_registry(*, transport: WebProviderTransport | None = None) -> WebProviderRegistry:
    from a13n_harness.providers.web.builtins import built_in_web_providers

    return WebProviderRegistry(built_in_web_providers(), transport=transport)
