"""Brave Web Search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import WebProviderError, WebSearchRequest, WebSearchResponse
from a13n_harness.providers.web.options import SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration, require_api_key
from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    key = require_api_key(credential)
    if len(request.query) > 600 or len(request.query.split()) > 75:
        raise WebProviderError("web_search_request_invalid")
    limit = min(request.limit, options.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            params={"q": request.query, "count": limit, "result_filter": "web", "text_decorations": "false"},
        ),
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
        lambda item: (item.get("title") or "", item["url"], item.get("description") or ""),
    )
