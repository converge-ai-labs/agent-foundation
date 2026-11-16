"""SpecStream Search and Extract."""

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

from ..configuration import ApiKeyCredential, EmptyConfiguration, require_api_key
from ..contracts import WebPolicy
from .common import SEARCH_RESPONSE_BYTES, scrape_response_bytes, scrape_result, search_response

if TYPE_CHECKING:
    from a13n_harness.providers.web.transport import WebProviderTransport

SEARCH_URL = "https://api.specstream.ai/v1/web-search"
SCRAPE_URL = "https://api.specstream.ai/v1/extract"
MAX_QUERY_CHARS = 2000
_SCRAPE_FAILURE_STATUSES = frozenset({"blocked", "not_found", "unsupported", "invalid_url", "failed"})


async def search(
    configuration: EmptyConfiguration,
    credential: ApiKeyCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    key = require_api_key(credential)
    if len(request.query) > MAX_QUERY_CHARS:
        raise WebProviderError("web_search_request_invalid")
    limit = min(request.limit, options.max_results)
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"q": request.query, "num": limit, "speed": "fast"},
        ),
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    data = _data_object(payload, "web_search_response_invalid")
    return search_response(
        data.get("sources"),
        lambda item: (_optional_text(item.get("title")), item["url"], _optional_text(item.get("snippet"))),
        usage=_usage(data, "web_search"),
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
            headers={"Authorization": f"Bearer {key}"},
            json={"urls": [request.url], "format": "markdown"},
        ),
        operation="scrape",
        max_response_bytes=scrape_response_bytes(limit),
    )
    data = _data_object(payload, "web_scrape_response_invalid")
    results = data.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise WebProviderError("web_scrape_response_invalid")
    item = results[0]
    status = item.get("status")
    if status in _SCRAPE_FAILURE_STATUSES:
        raise WebProviderError("web_scrape_failed")
    if status == "empty":
        content: object = "" if item.get("text") is None else item.get("text")
    elif status == "ok":
        content = item.get("text")
    else:
        raise WebProviderError("web_scrape_response_invalid")
    return scrape_result(
        content=content,
        canonical_url=item.get("url") or request.url,
        title=item.get("title"),
        request=request,
        usage=_usage(data, "extract"),
    )


def _data_object(payload: object, error_code: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise WebProviderError(error_code)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WebProviderError(error_code)
    return data


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("invalid text field")
    return value


def _usage(data: dict[str, object], product: str) -> tuple[ProviderUsage, ...]:
    price = data.get("price_usd")
    if type(price) not in {int, float}:
        return ()
    cost = Decimal(str(price))
    if not cost.is_finite() or cost < 0:
        return ()
    return (
        ProviderUsage(
            usage_id="usage_" + token_hex(16),
            provider="specstream",
            product=product,
            timestamp=datetime.now(UTC),
            cost=cost,
            currency="USD",
        ),
    )
