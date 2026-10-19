from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.providers.web import (
    ScrapeOptions,
    SearchOptions,
    WebProviderError,
    WebScrapeRequest,
    WebSearchRequest,
)
from a13n_harness.providers.web.errors import WebProviderResponseError
from a13n_harness.providers.web.transport import WebProviderTransport


def transport(handler) -> WebProviderTransport:
    return WebProviderTransport(client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


def scrape_request(*, url: str = "https://example.com/article", max_content_bytes: int = 1024) -> WebScrapeRequest:
    return WebScrapeRequest(url=url, max_content_bytes=max_content_bytes, deadline_seconds=30, max_redirects=0)


def search_item(title: str, url: str) -> dict:
    return {
        "title": title,
        "url": url,
        "snippet": "Short UI snippet",
        "summary": "Long model-facing summary",
        "content": "",
        "rerank_score": 2.4,
        "site_name": None,
        "site_icon": None,
        "date": None,
        "authority": -1,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("requested,configured,count", [(2, 5, 2), (5, 1, 1), (100, 100, 50)])
async def test_search_preserves_query_and_maps_bounded_results(providers, requested, configured, count) -> None:
    query = '深度求索 "exact phrase" site:example.com -site:private.example.com'

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"query": query, "count": count, "need_content": False}
        return httpx2.Response(
            200,
            json={
                "results": [
                    search_item("Other", "https://other.test/"),
                    search_item("Private", "https://private.example.com/"),
                    search_item("First", "https://example.com/1"),
                    search_item("Second", "https://example.com/2"),
                ]
            },
        )

    async with providers["deepseek"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=SearchOptions(
            max_results=configured,
            allow_domains=("example.com",),
            deny_domains=("private.example.com",),
        ),
    ) as web:
        result = await web.search(WebSearchRequest(query=query, limit=requested))
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("First", "https://example.com/1", "Long model-facing summary"),
        ("Second", "https://example.com/2", "Long model-facing summary"),
    ][:count]


@pytest.mark.anyio
async def test_search_accepts_128_unicode_characters_and_empty_results(providers) -> None:
    query = "界" * 128

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["query"] == query
        return httpx2.Response(200, json={"results": []})

    async with providers["deepseek"].open({}, {"api_key": "secret"}, transport=transport(handle)) as web:
        assert (await web.search(WebSearchRequest(query=query, limit=1))).results == ()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_vendor_length_limits_reject_without_dispatch(providers, operation) -> None:
    async with providers["deepseek"].open(
        {}, {"api_key": "secret"}, transport=transport(lambda _: pytest.fail("invalid request dispatched"))
    ) as web:
        with pytest.raises(WebProviderError, match=f"^web_{operation}_request_invalid$"):
            if operation == "search":
                await web.search(WebSearchRequest(query="界" * 129, limit=1))
            else:
                await web.scrape(scrape_request(url="https://example.com/" + "a" * 8192))


@pytest.mark.anyio
@pytest.mark.parametrize("title", ["Article", ""])
async def test_fetch_maps_markdown_and_preserves_utf8_budget(providers, policy, title) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"url": "https://example.com/article"}
        return httpx2.Response(200, json={"title": title, "content": "# 界界"})

    async with providers["deepseek"].open(
        {}, {"api_key": "secret"}, transport=transport(handle), options=ScrapeOptions(max_content_bytes=6)
    ) as web:
        result = await web.scrape(scrape_request(), policy=policy)
    assert result.content == "# 界"
    assert result.title == title
    assert result.source_url == result.canonical_url == "https://example.com/article"
    assert result.truncated


@pytest.mark.anyio
async def test_fetch_accepts_maximum_url_length(providers) -> None:
    url = "https://example.com/".ljust(8192, "a")

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content) == {"url": url}
        return httpx2.Response(200, json={"title": "", "content": "Page"})

    async with providers["deepseek"].open({}, {"api_key": "secret"}, transport=transport(handle)) as web:
        assert (await web.scrape(scrape_request(url=url))).content == "Page"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation,payload",
    [
        ("search", []),
        ("search", {}),
        ("search", {"results": [search_item("Bad", "file:///private")]}),
        ("search", {"results": [{**search_item("Bad", "https://example.com/"), "summary": None}]}),
        ("scrape", []),
        ("scrape", {"title": "Missing content"}),
        ("scrape", {"content": "Missing title"}),
        ("scrape", {"title": None, "content": "Page"}),
        ("scrape", {"title": "Title", "content": 1}),
    ],
)
async def test_malformed_responses_fail_safely(providers, operation, payload) -> None:
    async with providers["deepseek"].open(
        {}, {"api_key": "secret"}, transport=transport(lambda _: httpx2.Response(200, json=payload))
    ) as web:
        with pytest.raises(WebProviderError, match=f"^web_{operation}_response_invalid$"):
            if operation == "search":
                await web.search(WebSearchRequest(query="query", limit=1))
            else:
                await web.scrape(scrape_request())


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_rate_limit_preserves_retry_delay_without_exposing_upstream_details(providers, operation) -> None:
    async with providers["deepseek"].open(
        {},
        {"api_key": "secret"},
        transport=transport(
            lambda _: httpx2.Response(
                429,
                headers={"Retry-After": "2"},
                json={"error": {"message": "private diagnostic secret", "type": "rate_limit_error"}},
            )
        ),
    ) as web:
        with pytest.raises(WebProviderResponseError, match=f"^web_{operation}_rate_limited$") as raised:
            if operation == "search":
                await web.search(WebSearchRequest(query="query", limit=1))
            else:
                await web.scrape(scrape_request())
    assert raised.value.retry_after_seconds == 2


@pytest.mark.anyio
async def test_fetch_refuses_domain_restrictions_before_dispatch(providers) -> None:
    async with providers["deepseek"].open(
        {},
        {"api_key": "secret"},
        options=ScrapeOptions(allow_domains=("example.com",)),
        transport=transport(lambda _: pytest.fail("restricted scrape dispatched")),
    ) as web:
        with pytest.raises(WebProviderError, match=r"^web_scrape_domain_restrictions_unsupported$"):
            await web.scrape(scrape_request())
