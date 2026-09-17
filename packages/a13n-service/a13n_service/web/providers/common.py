"""Shared bounds and provider-neutral result conversion."""

from __future__ import annotations

from collections.abc import Callable

from a13n_harness.capabilities.web import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.usage import ProviderUsage

from a13n_service.web.domain import MAX_SCRAPE_CONTENT_BYTES, ScrapeSelection, SearchSelection

SEARCH_RESPONSE_BYTES = 1024 * 1024
SCRAPE_RESPONSE_OVERHEAD_BYTES = 256 * 1024
MAX_JSON_BYTES_PER_CONTENT_BYTE = 6


def scrape_response_bytes(content_bytes: int) -> int:
    if not 1 <= content_bytes <= MAX_SCRAPE_CONTENT_BYTES:
        raise ValueError("scrape content budget is invalid")
    return SCRAPE_RESPONSE_OVERHEAD_BYTES + (content_bytes + 1) * MAX_JSON_BYTES_PER_CONTENT_BYTE


def search_response(
    rows: object,
    selection: SearchSelection,
    limit: int,
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
            if selection.allows(result.url):
                results.append(result)
        return WebSearchResponse(results=tuple(results[:limit]), usage=usage)
    except (ValueError, TypeError, KeyError) as error:
        raise WebProviderError("web_search_response_invalid") from error


def scrape_result(
    *,
    content: object,
    canonical_url: object,
    title: object,
    request: WebScrapeRequest,
    selection: ScrapeSelection,
    usage: tuple[ProviderUsage, ...] = (),
) -> WebScrapeResult:
    try:
        if not isinstance(content, str) or not isinstance(canonical_url, str):
            raise ValueError("invalid scrape content or URL")
        if title is not None and not isinstance(title, str):
            raise ValueError("invalid scrape title")
        encoded = content.encode("utf-8")
        limit = min(request.max_content_bytes, selection.max_content_bytes)
        return WebScrapeResult(
            content=encoded[:limit].decode("utf-8", errors="ignore"),
            source_url=request.url,
            canonical_url=canonical_url,
            title=title,
            truncated=len(encoded) > limit,
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
