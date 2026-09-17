"""Credential-free DuckDuckGo HTML search."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urljoin, urlsplit

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse, WebSearchResult

from a13n_service.web.domain import SearchSelection

from .common import SEARCH_RESPONSE_BYTES

if TYPE_CHECKING:
    from a13n_service.web.adapters import WebProviderTransport

SEARCH_URL = "https://html.duckduckgo.com/html/"


async def search(
    transport: WebProviderTransport, _key: str, request: WebSearchRequest, selection: SearchSelection
) -> WebSearchResponse:
    content = await transport.exchange(
        lambda client: client.build_request("GET", SEARCH_URL, params={"q": request.query}),
        endpoint=SEARCH_URL,
        operation="search",
        max_response_bytes=SEARCH_RESPONSE_BYTES,
    )
    try:
        parser = DuckDuckGoParser()
        parser.feed(content.decode("utf-8"))
        limit = min(request.limit, selection.max_results)
        return WebSearchResponse(
            results=tuple(result for result in parser.results if selection.allows(result.url))[:limit]
        )
    except (UnicodeError, ValueError) as error:
        raise WebProviderError("web_search_response_invalid") from error


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
            url = _result_url(self._href)
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


def _result_url(value: str | None) -> str | None:
    if value is None:
        return None
    absolute = urljoin("https://duckduckgo.com", value)
    target = parse_qs(urlsplit(absolute).query).get("uddg", [absolute])[0]
    parsed = urlsplit(target)
    return target if parsed.scheme in {"http", "https"} and parsed.hostname else None
