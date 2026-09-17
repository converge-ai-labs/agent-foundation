from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.capabilities.web import WebScrapeRequest, WebSearchRequest
from a13n_service.provider_plugins.builtins import ApiKeyCredential, EmptyConfiguration
from a13n_service.web.adapters import WebProviderTransport
from a13n_service.web.providers import duckduckgo, firecrawl, jina, parallel, perplexity, serpapi, tavily

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
        assert endpoint in {*SEARCH_URLS.values(), *SCRAPE_URLS.values()}
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
async def test_vendor_search(provider: str, payload: object) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url).startswith(SEARCH_URLS[provider])
        if provider == "serpapi":
            assert request.url.params["api_key"] == "secret"
        elif provider == "parallel":
            assert request.headers["x-api-key"] == "secret"
        else:
            assert request.headers["authorization"] == "Bearer secret"
        return httpx2.Response(200, json=payload)

    result = await transport(handle).search_registered(
        provider,
        configuration={},
        credentials=ApiKeyCredential(api_key="secret"),
        request=WebSearchRequest(query="query", limit=2),
        max_results=2,
        allow_domains=("example.com",),
        deny_domains=(),
    )
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("Good", "https://example.com/a", "Summary")
    ]


@pytest.mark.anyio
async def test_duckduckgo_uses_html_search_without_credential() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["q"] == "query"
        assert "authorization" not in request.headers
        return httpx2.Response(
            200,
            text='<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Good</a>'
            '<a class="result__snippet">Summary</a>',
        )

    result = await transport(handle).search_registered(
        "duckduckgo",
        configuration={},
        credentials=EmptyConfiguration(),
        request=WebSearchRequest(query="query", limit=2),
        max_results=2,
        allow_domains=("example.com",),
        deny_domains=(),
    )
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("Good", "https://example.com/a", "Summary")
    ]


@pytest.mark.anyio
async def test_serpapi_can_return_no_organic_results() -> None:
    result = await transport(
        lambda _request: httpx2.Response(200, json={"search_metadata": {"status": "Success"}})
    ).search_registered(
        "serpapi",
        configuration={},
        credentials=ApiKeyCredential(api_key="secret"),
        request=WebSearchRequest(query="query", limit=2),
        max_results=2,
        allow_domains=(),
        deny_domains=(),
    )
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
async def test_vendor_scrape(provider: str, payload: object) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url).startswith(SCRAPE_URLS[provider])
        if provider == "parallel":
            assert json.loads(request.content)["advanced_settings"]["full_content"] is True
        return httpx2.Response(200, json=payload)

    result = await transport(handle).scrape_registered(
        provider,
        configuration={},
        credentials=ApiKeyCredential(api_key="secret"),
        request=WebScrapeRequest(
            url="https://example.com/a", max_content_bytes=4, deadline_seconds=30, max_redirects=0
        ),
        policy=None,
        max_content_bytes=4,
    )
    assert result.content == "Cont"
    assert result.truncated
    assert result.source_url == "https://example.com/a"
