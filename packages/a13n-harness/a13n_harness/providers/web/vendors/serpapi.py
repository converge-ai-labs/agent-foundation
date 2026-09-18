"""SerpApi Google organic search."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import WebProviderError, WebSearchRequest, WebSearchResponse
from a13n_harness.providers.web.options import SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration
from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://serpapi.com/search"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    key = credential.api_key.get_secret_value()
    limit = min(request.limit, options.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            params={"engine": "google", "q": request.query, "num": limit, "api_key": key},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("organic_results", []),
        lambda item: (item.get("title") or "", item["link"], item.get("snippet") or ""),
    )
