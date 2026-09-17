"""Tavily Search and Extract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.capabilities.web import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)

from a13n_service.web.domain import ScrapeSelection, SearchSelection

from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response, single_scrape_result

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://api.tavily.com/search"
SCRAPE_URL = "https://api.tavily.com/extract"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": request.query, "search_depth": "basic", "max_results": limit},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("results"),
        selection,
        limit,
        lambda item: (item.get("title") or "", item["url"], item.get("content") or ""),
    )


async def scrape(
    transport: WebProviderTransport, key: str, request: WebScrapeRequest, selection: ScrapeSelection
) -> WebScrapeResult:
    limit = min(request.max_content_bytes, selection.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SCRAPE_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"urls": [request.url], "extract_depth": "basic", "format": "markdown"},
        ),
        endpoint=SCRAPE_URL,
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    item = single_scrape_result(payload)
    return scrape_result(
        content=item.get("raw_content"),
        canonical_url=item.get("url", request.url),
        title=item.get("title"),
        request=request,
        selection=selection,
    )
