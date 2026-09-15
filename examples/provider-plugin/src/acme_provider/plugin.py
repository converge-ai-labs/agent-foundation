"""One installed entry point registering Web, Model, and Memory Provider types."""

from __future__ import annotations

from a13n_harness.capabilities.web import (
    WebPolicy,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.memory_plugins import Mem0OSSPlugin
from a13n_service.provider_plugins import (
    ProviderConfiguration,
    ProviderIntegration,
    ProviderPluginRegistry,
    RuntimeProvider,
    WebProviderRegistration,
    provider_plugin,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.providers.openai import OpenAIProvider


class AcmeWebConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: str = Field(default="docs", pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class AcmeCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True})


class AcmeWebRuntime:
    """Offline adapter used by the example so conformance needs no vendor account."""

    async def search(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebSearchRequest,
        max_results: int,
        allow_domains: tuple[str, ...],
        deny_domains: tuple[str, ...],
    ) -> WebSearchResponse:
        del credentials, allow_domains, deny_domains
        config = AcmeWebConfiguration.model_validate(configuration)
        return WebSearchResponse(
            results=(
                WebSearchResult(
                    title=f"{config.index}: {request.query}",
                    url="https://docs.example.com/result",
                    snippet="Installed Provider package result",
                ),
            )[:max_results]
        )

    async def scrape(
        self,
        *,
        configuration: BaseModel,
        credentials: BaseModel,
        request: WebScrapeRequest,
        policy: WebPolicy,
        max_content_bytes: int,
    ) -> WebScrapeResult:
        del configuration, credentials
        await policy.authorize(request.url, purpose="scrape")
        content = "Installed Provider package content"
        return WebScrapeResult(
            content=content[:max_content_bytes],
            source_url=request.url,
            canonical_url=request.url,
            truncated=len(content.encode()) > max_content_bytes,
        )

    async def aclose(self) -> None:
        pass


class AcmeModelConfiguration(ProviderConfiguration):
    base_url: str | None = "https://models.example.com/v1"


def _build_model_provider(provider: RuntimeProvider, http_client, model_api: str):
    del model_api
    return OpenAIProvider(
        base_url=provider.endpoint,
        api_key=provider.credential or "",
        http_client=http_client,
    )


class AcmeMemoryPlugin(Mem0OSSPlugin):
    """Reuse the native OSS adapter and schemas in both Harness and Service."""

    key = "acme.memory"
    display_name = "Acme Memory"


@provider_plugin(api_version=1)
def register(registry: ProviderPluginRegistry) -> None:
    registry.memory.register(AcmeMemoryPlugin())
    registry.web.register(
        WebProviderRegistration(
            type="acme_web",
            display_name="Acme Web",
            configuration_model=AcmeWebConfiguration,
            credential_model=AcmeCredential,
            setup_url="https://docs.example.com/provider-setup",
            factory=AcmeWebRuntime,
            supports_search=True,
            supports_scrape=True,
            supports_restricted_scrape=True,
        )
    )
    registry.model.register(
        ProviderIntegration(
            type="acme_model",
            display_name="Acme Model",
            configuration_model=AcmeModelConfiguration,
            supported_model_apis=("openai.responses",),
            build_provider=_build_model_provider,
            endpoint="https://models.example.com/v1",
        )
    )
