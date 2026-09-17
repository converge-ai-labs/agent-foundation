"""Firecrawl Search and Scrape."""

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

from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://api.firecrawl.dev/v2/search"
SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": request.query, "limit": limit, "sources": [{"type": "web"}]},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise WebProviderError("web_search_response_invalid")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        data.get("web"),
        selection,
        limit,
        lambda item: (item.get("title") or "", item["url"], item.get("description") or ""),
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
            json={"url": request.url, "formats": ["markdown"], "onlyMainContent": True, "timeout": 25000},
        ),
        endpoint=SCRAPE_URL,
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise WebProviderError("web_scrape_response_invalid")
    item = payload.get("data")
    if not isinstance(item, dict):
        raise WebProviderError("web_scrape_response_invalid")
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise WebProviderError("web_scrape_response_invalid")
    return scrape_result(
        content=item.get("markdown"),
        canonical_url=metadata.get("sourceURL", request.url),
        title=metadata.get("title"),
        request=request,
        selection=selection,
    )
