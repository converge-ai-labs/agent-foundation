"""DeepSeek Search and Fetch: https://api-docs.deepseek.com/guides/search/."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.web.contracts import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration, require_api_key
from ..contracts import WebPolicy
from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    key = require_api_key(credential)
    if len(request.query) > 128:
        raise WebProviderError("web_search_request_invalid")
    limit = min(request.limit, options.max_results, 50)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": request.query, "count": limit, "need_content": False},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    # DeepSeek's summary is intended for model context; snippet is its shorter UI text.
    return search_response(
        payload.get("results"),
        lambda item: (item["title"], item["url"], item["summary"]),
    )


async def scrape(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebScrapeRequest,
    options: ScrapeOptions,
    transport: WebProviderTransport,
    policy: WebPolicy,
) -> WebScrapeResult:
    key = require_api_key(credential)
    if len(request.url) > 8192:
        raise WebProviderError("web_scrape_request_invalid")
    limit = min(request.max_content_bytes, options.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            FETCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"url": request.url},
        ),
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("title"), str):
        raise WebProviderError("web_scrape_response_invalid")
    return scrape_result(
        content=payload.get("content"),
        canonical_url=request.url,
        title=payload["title"],
        request=request,
    )
