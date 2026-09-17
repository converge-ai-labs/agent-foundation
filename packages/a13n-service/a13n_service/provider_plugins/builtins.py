"""Built-in Providers registered through the deployment extension contract."""

from __future__ import annotations

from a13n_harness.memory_plugins import Mem0OSSBackendPlugin, Mem0PlatformBackendPlugin
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
    registry.memory.register(Mem0OSSBackendPlugin())
    registry.memory.register(Mem0PlatformBackendPlugin())
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
    for provider_type, display_name, setup_url, search, scrape, key_required in (
        ("brave", "Brave Search", "https://api-dashboard.search.brave.com/", True, False, True),
        ("exa", "Exa", "https://dashboard.exa.ai/api-keys", True, True, True),
        ("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/", True, False, False),
        ("parallel", "Parallel", "https://platform.parallel.ai/", True, True, True),
        ("tavily", "Tavily", "https://app.tavily.com/", True, True, True),
        ("firecrawl", "Firecrawl", "https://www.firecrawl.dev/app", True, True, True),
        ("jina", "Jina", "https://jina.ai/reader/", True, True, True),
        ("perplexity", "Perplexity", "https://www.perplexity.ai/settings/api", True, False, True),
        ("serpapi", "SerpApi", "https://serpapi.com/manage-api-key", True, False, True),
    ):
        registry.web.register(
            WebProviderRegistration(
                type=provider_type,
                display_name=display_name,
                configuration_model=EmptyConfiguration,
                credential_model=ApiKeyCredential if key_required else EmptyConfiguration,
                credential_required=key_required,
                setup_url=setup_url,
                factory=lambda provider_type=provider_type: BoundWebProviderRuntime(provider_type),
                supports_search=search,
                supports_scrape=scrape,
            )
        )
