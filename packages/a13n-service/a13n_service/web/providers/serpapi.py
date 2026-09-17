"""SerpApi Google organic search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse

from a13n_service.web.domain import SearchSelection

from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://serpapi.com/search"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            params={"engine": "google", "q": request.query, "num": limit, "api_key": key},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("organic_results", []),
        selection,
        limit,
        lambda item: (item.get("title") or "", item["link"], item.get("snippet") or ""),
    )
