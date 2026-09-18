"""Jina Search and Reader."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration
from ..contracts import WebPolicy
from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://s.jina.ai/"
SCRAPE_URL = "https://r.jina.ai/"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    assert credential is not None
    key = credential.api_key.get_secret_value()
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            params={"q": request.query},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("data"),
        lambda item: (item.get("title") or "", item["url"], item.get("content") or ""),
    )


async def scrape(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebScrapeRequest,
    options: ScrapeOptions,
    transport: WebProviderTransport,
    policy: WebPolicy,
) -> WebScrapeResult:
    assert credential is not None
    key = credential.api_key.get_secret_value()
    limit = min(request.max_content_bytes, options.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            f"{SCRAPE_URL}{request.url}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        ),
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise WebProviderError("web_scrape_response_invalid")
    item = payload["data"]
    return scrape_result(
        content=item.get("content"),
        canonical_url=item.get("url", request.url),
        title=item.get("title"),
        request=request,
    )
