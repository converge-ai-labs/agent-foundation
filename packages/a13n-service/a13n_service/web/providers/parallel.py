"""Parallel Search and Extract."""

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

SEARCH_URL = "https://api.parallel.ai/v1/search"
SCRAPE_URL = "https://api.parallel.ai/v1/extract"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"x-api-key": key},
            json={"search_queries": [request.query], "mode": "basic", "advanced_settings": {"max_results": limit}},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(payload.get("results"), selection, limit, _search_item)


def _search_item(item: dict[str, object]) -> tuple[object, object, object]:
    excerpts = item.get("excerpts", [])
    if not isinstance(excerpts, list) or not all(isinstance(part, str) for part in excerpts):
        raise ValueError("invalid excerpts")
    return item.get("title") or "", item["url"], "\n".join(excerpts)


async def scrape(
    transport: WebProviderTransport, key: str, request: WebScrapeRequest, selection: ScrapeSelection
) -> WebScrapeResult:
    limit = min(request.max_content_bytes, selection.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SCRAPE_URL,
            headers={"x-api-key": key},
            json={"urls": [request.url], "advanced_settings": {"full_content": True}},
        ),
        endpoint=SCRAPE_URL,
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    item = single_scrape_result(payload)
    return scrape_result(
        content=item.get("full_content"),
        canonical_url=item.get("url", request.url),
        title=item.get("title"),
        request=request,
        selection=selection,
    )
