"""Jina Search and Reader."""

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

SEARCH_URL = "https://s.jina.ai/"
SCRAPE_URL = "https://r.jina.ai/"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            params={"q": request.query},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("data"),
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
            "GET",
            f"{SCRAPE_URL}{request.url}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        ),
        endpoint=SCRAPE_URL,
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
        selection=selection,
    )
