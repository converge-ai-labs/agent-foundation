"""Official fixed-endpoint adapters for built-in Web Providers."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx2
from a13n_harness.capabilities.web import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)

from .adapters import SEARCH_RESPONSE_BYTES, _scrape_response_bytes
from .domain import ScrapeSelection, SearchSelection

if TYPE_CHECKING:
    from .adapters import WebProviderTransport

SEARCH_URLS = {
    "duckduckgo": "https://html.duckduckgo.com/html/",
    "parallel": "https://api.parallel.ai/v1/search",
    "tavily": "https://api.tavily.com/search",
    "firecrawl": "https://api.firecrawl.dev/v2/search",
    "jina": "https://s.jina.ai/",
    "perplexity": "https://api.perplexity.ai/search",
    "serpapi": "https://serpapi.com/search",
}
SCRAPE_URLS = {
    "parallel": "https://api.parallel.ai/v1/extract",
    "tavily": "https://api.tavily.com/extract",
    "firecrawl": "https://api.firecrawl.dev/v2/scrape",
    "jina": "https://r.jina.ai/",
}


async def search(
    transport: WebProviderTransport,
    provider: str,
    key: str,
    request: WebSearchRequest,
    selection: SearchSelection,
) -> WebSearchResponse:
    endpoint = SEARCH_URLS.get(provider)
    if endpoint is None:
        raise WebProviderError("web_search_unavailable")
    limit = min(request.limit, selection.max_results)
    if provider == "duckduckgo":
        content = await transport.exchange(
            lambda client: client.build_request("GET", endpoint, params={"q": request.query}),
            endpoint=endpoint,
            operation="search",
            max_response_bytes=SEARCH_RESPONSE_BYTES,
        )
        try:
            parser = DuckDuckGoParser()
            parser.feed(content.decode("utf-8"))
            return WebSearchResponse(
                results=tuple(result for result in parser.results if selection.allows(result.url))[:limit]
            )
        except (UnicodeError, ValueError) as error:
            raise WebProviderError("web_search_response_invalid") from error

    headers, body = _search_request(provider, key, request.query, limit)

    def build_search_request(client: httpx2.AsyncClient) -> httpx2.Request:
        if provider in {"jina", "serpapi"}:
            return client.build_request("GET", endpoint, headers=headers, params={k: str(v) for k, v in body.items()})
        return client.build_request("POST", endpoint, headers=headers, json=body)

    payload = await transport.exchange_json(
        build_search_request,
        endpoint=endpoint,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    try:
        items = _search_items(provider, payload)
        if len(items) > 100:
            raise ValueError("too many search results")
        results = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid search result")
            url = item.get("link") if provider == "serpapi" else item.get("url")
            if not isinstance(url, str):
                raise ValueError("invalid search URL")
            snippet = _snippet(provider, item)
            title = item.get("title") or ""
            if not isinstance(title, str):
                raise ValueError("invalid search title")
            result = WebSearchResult(title=title[:4096], url=url, snippet=snippet[: 16 * 1024])
            if selection.allows(result.url):
                results.append(result)
        return WebSearchResponse(results=tuple(results[:limit]))
    except (ValueError, TypeError, KeyError) as error:
        raise WebProviderError("web_search_response_invalid") from error


def _search_request(provider: str, key: str, query: str, limit: int) -> tuple[dict[str, str], dict[str, object]]:
    if provider == "parallel":
        return {"x-api-key": key}, {
            "search_queries": [query],
            "mode": "basic",
            "advanced_settings": {"max_results": limit},
        }
    if provider == "tavily":
        return {"Authorization": f"Bearer {key}"}, {"query": query, "search_depth": "basic", "max_results": limit}
    if provider == "firecrawl":
        return {"Authorization": f"Bearer {key}"}, {"query": query, "limit": limit, "sources": [{"type": "web"}]}
    if provider == "jina":
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}, {"q": query}
    if provider == "perplexity":
        return {"Authorization": f"Bearer {key}"}, {"query": query, "max_results": limit}
    if provider == "serpapi":
        return {}, {"engine": "google", "q": query, "num": limit, "api_key": key}
    raise ValueError("unknown provider")


def _search_items(provider: str, payload: object) -> list[object]:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    if provider == "firecrawl":
        if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
            raise ValueError("search failed")
        items = payload["data"].get("web")
    elif provider == "serpapi":
        if payload.get("error"):
            raise ValueError("search failed")
        items = payload.get("organic_results", [])
    else:
        items = payload.get("data") if provider == "jina" else payload.get("results")
    if not isinstance(items, list):
        raise ValueError("missing search results")
    return items


def _snippet(provider: str, item: dict[str, object]) -> str:
    if provider == "parallel":
        excerpts = item.get("excerpts", [])
        if not isinstance(excerpts, list) or not all(isinstance(part, str) for part in excerpts):
            raise ValueError("invalid excerpts")
        return "\n".join(excerpts)
    field = {
        "tavily": "content",
        "firecrawl": "description",
        "jina": "content",
        "perplexity": "snippet",
        "serpapi": "snippet",
    }[provider]
    value = item.get(field) or ""
    if not isinstance(value, str):
        raise ValueError("invalid snippet")
    return value


async def scrape(
    transport: WebProviderTransport,
    provider: str,
    key: str,
    request: WebScrapeRequest,
    selection: ScrapeSelection,
) -> WebScrapeResult:
    endpoint = SCRAPE_URLS.get(provider)
    if endpoint is None:
        raise WebProviderError("web_scrape_unavailable")
    if selection.restricted:
        raise WebProviderError("web_scrape_domain_restrictions_unsupported")
    limit = min(request.max_content_bytes, selection.max_content_bytes)
    headers, body = _scrape_request(provider, key, request.url)

    def build_scrape_request(client: httpx2.AsyncClient) -> httpx2.Request:
        if provider == "jina":
            return client.build_request("GET", f"{endpoint}{request.url}", headers=headers)
        return client.build_request("POST", endpoint, headers=headers, json=body)

    payload = await transport.exchange_json(
        build_scrape_request,
        endpoint=endpoint,
        operation="scrape",
        max_response_bytes=_scrape_response_bytes(limit),
    )
    try:
        item = _scrape_item(provider, payload)
        content_field = {
            "parallel": "full_content",
            "tavily": "raw_content",
            "firecrawl": "markdown",
            "jina": "content",
        }[provider]
        content = item.get(content_field)
        if not isinstance(content, str):
            raise ValueError("missing scrape content")
        encoded = content.encode("utf-8")
        title = item.get("title")
        canonical_url = item.get("url", request.url)
        if provider == "firecrawl":
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                raise ValueError("missing metadata")
            title = metadata.get("title")
            canonical_url = metadata.get("sourceURL", request.url)
        if title is not None and not isinstance(title, str):
            raise ValueError("invalid title")
        if not isinstance(canonical_url, str):
            raise ValueError("invalid canonical URL")
        return WebScrapeResult(
            content=encoded[:limit].decode("utf-8", errors="ignore"),
            source_url=request.url,
            canonical_url=canonical_url,
            title=title,
            truncated=len(encoded) > limit,
        )
    except (ValueError, TypeError, KeyError) as error:
        raise WebProviderError("web_scrape_response_invalid") from error


def _scrape_request(provider: str, key: str, url: str) -> tuple[dict[str, str], dict[str, object]]:
    if provider == "parallel":
        return {"x-api-key": key}, {"urls": [url], "advanced_settings": {"full_content": True}}
    if provider == "tavily":
        return {"Authorization": f"Bearer {key}"}, {"urls": [url], "extract_depth": "basic", "format": "markdown"}
    if provider == "firecrawl":
        return {"Authorization": f"Bearer {key}"}, {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": 25000,
        }
    if provider == "jina":
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}, {}
    raise ValueError("unknown provider")


def _scrape_item(provider: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    if provider == "firecrawl":
        if payload.get("success") is not True:
            raise ValueError("scrape failed")
        item = payload.get("data")
    elif provider == "jina":
        item = payload.get("data")
    else:
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError("missing scrape result")
        item = results[0]
    if not isinstance(item, dict):
        raise ValueError("invalid scrape result")
    return item


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebSearchResult] = []
        self._active: str | None = None
        self._href: str | None = None
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._pending: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.results) >= 100:
            return
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._active = "title"
            self._href = values.get("href")
            self._title = []
        elif "result__snippet" in classes:
            self._active = "snippet"
            self._snippet = []

    def handle_data(self, data: str) -> None:
        if self._active == "title":
            self._title.append(data)
        elif self._active == "snippet":
            self._snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._active == "title" and tag == "a":
            url = _duckduckgo_url(self._href)
            title = " ".join("".join(self._title).split())
            self._pending = (title[:4096], url) if title and url else None
            self._active = None
        elif self._active == "snippet" and tag in {"a", "div", "span"}:
            if self._pending:
                title, url = self._pending
                self.results.append(
                    WebSearchResult(title=title, url=url, snippet=" ".join("".join(self._snippet).split())[: 16 * 1024])
                )
                self._pending = None
            self._active = None


def _duckduckgo_url(value: str | None) -> str | None:
    if value is None:
        return None
    absolute = urljoin("https://duckduckgo.com", value)
    target = parse_qs(urlsplit(absolute).query).get("uddg", [absolute])[0]
    parsed = urlsplit(target)
    return target if parsed.scheme in {"http", "https"} and parsed.hostname else None
