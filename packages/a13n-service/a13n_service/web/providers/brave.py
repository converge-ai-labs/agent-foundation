"""Brave Web Search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse

from a13n_service.web.domain import SearchSelection

from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def search(
    transport: WebProviderTransport, key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    if len(request.query) > 600 or len(request.query.split()) > 75:
        raise WebProviderError("web_search_request_invalid")
    limit = min(request.limit, selection.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            params={"q": request.query, "count": limit, "result_filter": "web", "text_decorations": "false"},
        ),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    web = payload.get("web")
    if web is None and payload.get("type") == "search":
        rows = []
    elif isinstance(web, dict):
        rows = web.get("results")
    else:
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        rows,
        selection,
        limit,
        lambda item: (item.get("title") or "", item["url"], item.get("description") or ""),
    )
