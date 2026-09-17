"""Perplexity Search API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse

from a13n_service.web.domain import SearchSelection

from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://api.perplexity.ai/search"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": request.query, "max_results": limit},
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
        lambda item: (item.get("title") or "", item["url"], item.get("snippet") or ""),
    )
