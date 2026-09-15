"""Built-in Providers registered through the deployment extension contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_service.connectivity.connectors.providers.composio.configuration import (
    ComposioConfiguration,
    validate_setup,
)
from a13n_service.connectivity.connectors.providers.composio.runtime import ComposioProvider
from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.models.provider_adapters.registry import BUILT_IN_PROVIDER_INTEGRATIONS
from a13n_service.web.adapters import BoundWebProviderRuntime

from .api import ConnectorProviderRegistration, ProviderPluginRegistry, WebProviderRegistration


class EmptyConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiKeyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str = Field(min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True})

    @field_validator("api_key")
    @classmethod
    def bounded_nonblank_key(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 4096:
            raise ValueError("api_key must be nonblank and at most 4096 UTF-8 bytes")
        return value


def register(registry: ProviderPluginRegistry) -> None:
    for integration in BUILT_IN_PROVIDER_INTEGRATIONS:
        registry.model.register(integration)
    registry.connector.register(
        ConnectorProviderRegistration(
            type="composio",
            display_name="Composio",
            configuration_model=ComposioConfiguration,
            credential_model=ApiKeyCredentials,
            setup_validator=validate_setup,
            factory=lambda http, configuration, credentials: ComposioProvider(
                http,
                ApiKeyCredentials.model_validate(credentials),
            ),
        )
    )
    for provider_type, display_name, setup_url, search, scrape in (
        ("brave", "Brave Search", "https://api-dashboard.search.brave.com/", True, False),
        ("exa", "Exa", "https://dashboard.exa.ai/api-keys", True, True),
    ):
        registry.web.register(
            WebProviderRegistration(
                type=provider_type,
                display_name=display_name,
                configuration_model=EmptyConfiguration,
                credential_model=ApiKeyCredential,
                setup_url=setup_url,
                factory=lambda provider_type=provider_type: BoundWebProviderRuntime(provider_type),
                supports_search=search,
                supports_scrape=scrape,
            )
        )
