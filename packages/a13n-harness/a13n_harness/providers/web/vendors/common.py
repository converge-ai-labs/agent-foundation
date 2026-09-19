"""Shared bounds and provider-neutral result conversion."""

from __future__ import annotations

from collections.abc import Callable

from a13n_harness.providers.usage import ProviderUsage
from a13n_harness.providers.web.contracts import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.providers.web.options import MAX_SCRAPE_CONTENT_BYTES

SEARCH_RESPONSE_BYTES = 1024 * 1024
SCRAPE_RESPONSE_OVERHEAD_BYTES = 256 * 1024
MAX_JSON_BYTES_PER_CONTENT_BYTE = 6


def scrape_response_bytes(content_bytes: int) -> int:
    if not 1 <= content_bytes <= MAX_SCRAPE_CONTENT_BYTES:
        raise ValueError("scrape content budget is invalid")
    return SCRAPE_RESPONSE_OVERHEAD_BYTES + (content_bytes + 1) * MAX_JSON_BYTES_PER_CONTENT_BYTE


def search_response(
    rows: object,
    extract: Callable[[dict[str, object]], tuple[object, object, object]],
    *,
    usage: tuple[ProviderUsage, ...] = (),
) -> WebSearchResponse:
    try:
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("invalid search results")
        results: list[WebSearchResult] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid search result")
            title, url, snippet = extract(row)
            if not isinstance(title, str) or not isinstance(url, str) or not isinstance(snippet, str):
                raise ValueError("invalid search fields")
            result = WebSearchResult(title=title[:4096], url=url, snippet=snippet[: 16 * 1024])
            results.append(result)
        return WebSearchResponse(results=tuple(results), usage=usage)
    except (ValueError, TypeError, KeyError) as error:
        raise WebProviderError("web_search_response_invalid") from error


def scrape_result(
    *,
    content: object,
    canonical_url: object,
    title: object,
    request: WebScrapeRequest,
    usage: tuple[ProviderUsage, ...] = (),
) -> WebScrapeResult:
    try:
        if not isinstance(content, str) or not isinstance(canonical_url, str):
            raise ValueError("invalid scrape content or URL")
        if title is not None and not isinstance(title, str):
            raise ValueError("invalid scrape title")
        return WebScrapeResult(
            content=content,
            source_url=request.url,
            canonical_url=canonical_url,
            title=title,
            usage=usage,
        )
    except (ValueError, TypeError, KeyError) as error:
        raise WebProviderError("web_scrape_response_invalid") from error


def single_scrape_result(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise WebProviderError("web_scrape_response_invalid")
    rows = payload.get("results")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise WebProviderError("web_scrape_response_invalid")
    return rows[0]
