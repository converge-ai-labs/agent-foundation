"""Perplexity Search API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import WebProviderError, WebSearchRequest, WebSearchResponse
from a13n_harness.providers.web.options import SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration
from .common import SEARCH_RESPONSE_BYTES, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://api.perplexity.ai/search"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    assert credential is not None
    key = credential.api_key.get_secret_value()
    limit = min(request.limit, options.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": request.query, "max_results": limit},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("results"),
        lambda item: (item.get("title") or "", item["url"], item.get("snippet") or ""),
    )
