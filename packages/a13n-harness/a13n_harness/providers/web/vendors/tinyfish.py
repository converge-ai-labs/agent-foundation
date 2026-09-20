"""TinyFish Search and Fetch."""

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

SEARCH_URL = "https://api.search.tinyfish.ai"
SCRAPE_URL = "https://api.fetch.tinyfish.ai"


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    key = require_api_key(credential)
    params: dict[str, str] = {"query": request.query}
    if options.allow_domains:
        params["include_domains"] = ",".join(options.allow_domains)
    if options.deny_domains:
        params["exclude_domains"] = ",".join(options.deny_domains)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "GET",
            SEARCH_URL,
            headers={"X-API-Key": key},
            params=params,
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(
        payload.get("results"),
        lambda item: (item["title"], item["url"], item["snippet"]),
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
    limit = min(request.max_content_bytes, options.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SCRAPE_URL,
            headers={"X-API-Key": key},
            json={"urls": [request.url], "format": "markdown"},
        ),
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    item = _scrape_item(payload, request.url)
    return scrape_result(
        content=item.get("text"),
        canonical_url=item.get("final_url"),
        title=item.get("title"),
        request=request,
    )


def _scrape_item(payload: object, requested_url: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise WebProviderError("web_scrape_response_invalid")
    results = payload.get("results")
    errors = payload.get("errors")
    if not isinstance(results, list) or not isinstance(errors, list):
        raise WebProviderError("web_scrape_response_invalid")
    if not results and len(errors) == 1:
        error = errors[0]
        if isinstance(error, dict) and error.get("url") == requested_url and isinstance(error.get("error"), str):
            raise WebProviderError("web_scrape_failed")
        raise WebProviderError("web_scrape_response_invalid")
    if len(results) != 1 or errors or not isinstance(results[0], dict) or results[0].get("url") != requested_url:
        raise WebProviderError("web_scrape_response_invalid")
    return results[0]
