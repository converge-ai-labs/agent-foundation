from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.providers.web import ScrapeOptions, SearchOptions, WebScrapeRequest, WebSearchRequest
from a13n_harness.providers.web.configuration import ApiKeyCredential, EmptyConfiguration
from a13n_harness.providers.web.transport import WebProviderTransport
from a13n_harness.providers.web.vendors import duckduckgo, firecrawl, jina, parallel, perplexity, serpapi, tavily

SEARCH_URLS = {
    "duckduckgo": duckduckgo.SEARCH_URL,
    "parallel": parallel.SEARCH_URL,
    "tavily": tavily.SEARCH_URL,
    "firecrawl": firecrawl.SEARCH_URL,
    "jina": jina.SEARCH_URL,
    "perplexity": perplexity.SEARCH_URL,
    "serpapi": serpapi.SEARCH_URL,
}
SCRAPE_URLS = {
    "parallel": parallel.SCRAPE_URL,
    "tavily": tavily.SCRAPE_URL,
    "firecrawl": firecrawl.SCRAPE_URL,
    "jina": jina.SCRAPE_URL,
}


class FixedEndpointPolicy:
    async def validate(self, endpoint: str) -> str:
        assert (
            endpoint in {*SEARCH_URLS.values(), *SCRAPE_URLS.values()}
            or endpoint == "https://r.jina.ai/https://example.com/a"
        )
        return endpoint


def transport(handler) -> WebProviderTransport:
    return WebProviderTransport(
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        endpoint_policy=FixedEndpointPolicy(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("parallel", {"results": [{"title": "Good", "url": "https://example.com/a", "excerpts": ["Summary"]}]}),
        ("tavily", {"results": [{"title": "Good", "url": "https://example.com/a", "content": "Summary"}]}),
        (
            "firecrawl",
            {
                "success": True,
                "data": {"web": [{"title": "Good", "url": "https://example.com/a", "description": "Summary"}]},
            },
        ),
        ("jina", {"data": [{"title": "Good", "url": "https://example.com/a", "content": "Summary"}]}),
        ("perplexity", {"results": [{"title": "Good", "url": "https://example.com/a", "snippet": "Summary"}]}),
        ("serpapi", {"organic_results": [{"title": "Good", "link": "https://example.com/a", "snippet": "Summary"}]}),
    ],
)
async def test_vendor_search(provider: str, payload: object, providers) -> None:

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url).startswith(SEARCH_URLS[provider])
        if provider == "serpapi":
            assert request.url.params["api_key"] == "secret"
        elif provider == "parallel":
            assert request.headers["x-api-key"] == "secret"
        else:
            assert request.headers["authorization"] == "Bearer secret"
        return httpx2.Response(200, json=payload)

    async with providers[provider].open(
        {},
        ApiKeyCredential(api_key="secret"),
        transport=transport(handle),
        search_options=SearchOptions(max_results=2, allow_domains=("example.com",), deny_domains=()),
    ) as web:
        result = await web.search(WebSearchRequest(query="query", limit=2))
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("Good", "https://example.com/a", "Summary")
    ]


@pytest.mark.anyio
async def test_duckduckgo_uses_html_search_without_credential(providers) -> None:

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["q"] == "query"
        assert "authorization" not in request.headers
        return httpx2.Response(
            200,
            text='<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Good</a><a class="result__snippet">Summary</a>',
        )

    async with providers["duckduckgo"].open(
        {},
        EmptyConfiguration(),
        transport=transport(handle),
        search_options=SearchOptions(max_results=2, allow_domains=("example.com",), deny_domains=()),
    ) as web:
        result = await web.search(WebSearchRequest(query="query", limit=2))
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("Good", "https://example.com/a", "Summary")
    ]


@pytest.mark.anyio
async def test_serpapi_can_return_no_organic_results(providers) -> None:
    async with providers["serpapi"].open(
        {},
        ApiKeyCredential(api_key="secret"),
        transport=transport(lambda _request: httpx2.Response(200, json={"search_metadata": {"status": "Success"}})),
        search_options=SearchOptions(max_results=2, allow_domains=(), deny_domains=()),
    ) as web:
        result = await web.search(WebSearchRequest(query="query", limit=2))
    assert result.results == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("parallel", {"results": [{"url": "https://example.com/a", "title": "Title", "full_content": "Content"}]}),
        ("tavily", {"results": [{"url": "https://example.com/a", "title": "Title", "raw_content": "Content"}]}),
        ("firecrawl", {"success": True, "data": {"markdown": "Content", "metadata": {"title": "Title"}}}),
        ("jina", {"data": {"url": "https://example.com/a", "title": "Title", "content": "Content"}}),
    ],
)
async def test_vendor_scrape(provider: str, payload: object, providers, policy) -> None:

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url).startswith(SCRAPE_URLS[provider])
        if provider == "parallel":
            assert json.loads(request.content)["advanced_settings"]["full_content"] is True
        return httpx2.Response(200, json=payload)

    async with providers[provider].open(
        {},
        ApiKeyCredential(api_key="secret"),
        transport=transport(handle),
        scrape_options=ScrapeOptions(max_content_bytes=4),
    ) as web:
        result = await web.scrape(
            WebScrapeRequest(url="https://example.com/a", max_content_bytes=4, deadline_seconds=30, max_redirects=0),
            policy=policy,
        )
    assert result.content == "Cont"
    assert result.truncated
    assert result.source_url == "https://example.com/a"
