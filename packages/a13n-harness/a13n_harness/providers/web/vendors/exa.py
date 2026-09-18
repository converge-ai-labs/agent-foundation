"""Exa Search and Contents."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from secrets import token_hex
from typing import TYPE_CHECKING

from a13n_harness.providers.usage import ProviderUsage
from a13n_harness.providers.web.contracts import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions

from ..configuration import ApiKeyCredential, EmptyConfiguration
from ..contracts import WebPolicy
from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response, single_scrape_result

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://api.exa.ai/search"
SCRAPE_URL = "https://api.exa.ai/contents"


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
    body: dict[str, object] = {
        "query": request.query,
        "numResults": limit,
        "type": "auto",
        "contents": {"text": False, "highlights": True},
    }
    if options.allow_domains:
        body["includeDomains"] = list(options.allow_domains)
    if options.deny_domains:
        body["excludeDomains"] = list(options.deny_domains)
    payload = await transport.exchange_json(
        lambda client: client.build_request("POST", SEARCH_URL, headers={"x-api-key": key}, json=body),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    if not isinstance(payload, dict):
        raise WebProviderError("web_search_response_invalid")
    return search_response(payload.get("results"), _search_item, usage=_usage(payload, "search"))


def _search_item(item: dict[str, object]) -> tuple[object, object, object]:
    highlights = item.get("highlights")
    if highlights is not None and (
        not isinstance(highlights, list) or not all(isinstance(part, str) for part in highlights)
    ):
        raise ValueError("invalid Exa highlights")
    return item.get("title") or "", item["url"], "\n".join(highlights) if highlights else ""


async def scrape(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebScrapeRequest,
    options: ScrapeOptions,
    transport: WebProviderTransport,
    policy: WebPolicy,
) -> WebScrapeResult:
    assert credential is not None
    key = credential.api_key.get_secret_value()
    limit = min(request.max_content_bytes, options.max_content_bytes)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SCRAPE_URL,
            headers={"x-api-key": key},
            json={"urls": [request.url], "text": {"maxCharacters": limit + 1}},
        ),
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    item = single_scrape_result(payload)
    assert isinstance(payload, dict)
    return scrape_result(
        content=item.get("text"),
        canonical_url=item.get("url") or request.url,
        title=item.get("title"),
        request=request,
        usage=_usage(payload, "contents"),
    )


def _usage(payload: dict[str, object], product: str) -> tuple[ProviderUsage, ...]:
    costs = payload.get("costDollars")
    if not isinstance(costs, dict) or type(costs.get("total")) not in {int, float}:
        return ()
    cost = Decimal(str(costs["total"]))
    if not cost.is_finite() or cost < 0:
        return ()
    return (
        ProviderUsage(
            usage_id="usage_" + token_hex(16),
            provider="exa",
            product=product,
            timestamp=datetime.now(UTC),
            cost=cost,
            currency="USD",
        ),
    )
