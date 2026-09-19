"""Typed Web authoring: input models, operation callbacks, and one definition."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import ClassVar

from anyio import current_time, fail_after
from pydantic import BaseModel

from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from .contracts import (
    WebDomainPolicy,
    WebPolicy,
    WebProviderError,
    WebPurpose,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from .options import ScrapeOptions, SearchOptions
from .transport import WebProviderTransport

type SearchOperation[C: BaseModel, K: BaseModel] = Callable[
    [C, K | None, WebSearchRequest, SearchOptions, WebProviderTransport], Awaitable[WebSearchResponse]
]
type ScrapeOperation[C: BaseModel, K: BaseModel] = Callable[
    [C, K | None, WebScrapeRequest, ScrapeOptions, WebProviderTransport, WebPolicy], Awaitable[WebScrapeResult]
]


@dataclass(frozen=True, slots=True, kw_only=True)
class WebProviderDefinition[C: BaseModel, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Web"

    search: SearchOperation[C, K] | None = None
    scrape: ScrapeOperation[C, K] | None = None
    supports_restricted_scrape: bool = False

    def validate_domain(self) -> None:
        if self.search is None and self.scrape is None:
            raise ValueError("Web Provider must implement search or scrape")
        if self.supports_restricted_scrape and self.scrape is None:
            raise ValueError("restricted scrape requires a scrape operation")

    @property
    def supports_search(self) -> bool:
        return self.search is not None

    @property
    def supports_scrape(self) -> bool:
        return self.scrape is not None

    @asynccontextmanager
    async def open(
        self,
        configuration: object,
        credential: object = None,
        *,
        search_options: SearchOptions | None = None,
        scrape_options: ScrapeOptions | None = None,
        transport: WebProviderTransport | None = None,
    ) -> AsyncIterator[WebProvider[C, K]]:
        """Validate host inputs before constructing an operation handle; no I/O at open."""
        parsed = self.configuration_model.model_validate(configuration)
        yield WebProvider(
            self,
            parsed,
            self.parse_credential(parsed, credential),
            search_options or SearchOptions(),
            scrape_options or ScrapeOptions(),
            transport or WebProviderTransport(),
        )


class _PublicWebPolicy:
    async def authorize(self, url: str, *, purpose: WebPurpose) -> None:
        await EndpointPolicy().validate(url)


@dataclass(frozen=True, slots=True)
class WebProvider[C: BaseModel, K: BaseModel]:
    definition: WebProviderDefinition[C, K]
    configuration: C
    credential: K | None = field(repr=False)
    search_options: SearchOptions
    scrape_options: ScrapeOptions
    transport: WebProviderTransport

    @property
    def supports_domain_restrictions(self) -> bool:
        return self.definition.supports_restricted_scrape

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        operation = self.definition.search
        if operation is None:
            raise WebProviderError("web_search_unavailable")
        response = WebSearchResponse.model_validate(
            await operation(self.configuration, self.credential, request, self.search_options, self.transport)
        )
        return response.model_copy(
            update={
                "results": tuple(result for result in response.results if self.search_options.allows(result.url))[
                    : min(request.limit, self.search_options.max_results)
                ]
            }
        )

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy | None = None) -> WebScrapeResult:
        operation = self.definition.scrape
        if operation is None:
            raise WebProviderError("web_scrape_unavailable")
        if self.scrape_options.restricted and not self.supports_domain_restrictions:
            raise WebProviderError("web_scrape_domain_restrictions_unsupported")
        with fail_after(request.deadline_seconds) as scope:
            effective_policy = WebDomainPolicy(self.scrape_options, policy or _PublicWebPolicy())
            await effective_policy.authorize(request.url, purpose="scrape")
            result = WebScrapeResult.model_validate(
                await operation(
                    self.configuration, self.credential, request, self.scrape_options, self.transport, effective_policy
                )
            )
            encoded = result.content.encode("utf-8")
            if current_time() >= scope.deadline:
                raise TimeoutError
            limit = min(request.max_content_bytes, self.scrape_options.max_content_bytes)
            if len(encoded) > limit:
                return result.model_copy(
                    update={
                        "content": encoded[:limit].decode("utf-8", errors="ignore"),
                        "truncated": True,
                    }
                )
            return result
