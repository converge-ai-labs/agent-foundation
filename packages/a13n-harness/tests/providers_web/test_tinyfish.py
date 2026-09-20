from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.providers.web import (
    ScrapeOptions,
    SearchOptions,
    WebProviderError,
    WebProviderResponseError,
    WebScrapeRequest,
    WebSearchRequest,
)
from a13n_harness.providers.web.transport import WebProviderTransport
from a13n_harness.providers.web.vendors import tinyfish
from pydantic import ValidationError


class TinyFishEndpointPolicy:
    async def validate(self, endpoint: str) -> str:
        assert endpoint in {tinyfish.SEARCH_URL, tinyfish.SCRAPE_URL}
        return endpoint


def transport(handler) -> WebProviderTransport:
    return WebProviderTransport(
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        endpoint_policy=TinyFishEndpointPolicy(),
    )


def scrape_request(*, max_content_bytes: int = 1024) -> WebScrapeRequest:
    return WebScrapeRequest(
        url="https://example.com/article",
        max_content_bytes=max_content_bytes,
        deadline_seconds=30,
        max_redirects=0,
    )


def test_definition_uses_shared_empty_configuration_and_api_key_credential(providers) -> None:
    provider = providers["tinyfish"]
    assert provider.display_name == "TinyFish"
    assert provider.setup_url == "https://agent.tinyfish.ai/api-keys"
    assert provider.supports_search and provider.supports_scrape
    assert not provider.supports_restricted_scrape
    with pytest.raises(ValidationError):
        provider.configuration_model.model_validate({"endpoint": "https://example.com"})
    with pytest.raises(ValidationError):
        provider.credential_model.model_validate({"api_key": " "})


@pytest.mark.anyio
async def test_search_maps_request_response_and_domain_filters(providers) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert dict(request.url.params) == {
            "query": "find sources",
            "include_domains": "example.com",
            "exclude_domains": "private.example.com",
        }
        assert request.headers["x-api-key"] == "secret"
        return httpx2.Response(
            200,
            json={
                "query": "find sources",
                "results": [
                    {"title": "Wrong", "url": "https://other.test/", "snippet": "filtered"},
                    {"title": "First", "url": "https://example.com/1", "snippet": "one"},
                    {"title": "Second", "url": "https://example.com/2", "snippet": "two"},
                ],
                "total_results": 3,
                "page": 0,
            },
        )

    async with providers["tinyfish"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=SearchOptions(
            max_results=1,
            allow_domains=("example.com",),
            deny_domains=("private.example.com",),
        ),
    ) as web:
        result = await web.search(WebSearchRequest(query="find sources", limit=2))
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("First", "https://example.com/1", "one")
    ]


@pytest.mark.anyio
async def test_search_accepts_empty_results(providers) -> None:
    async with providers["tinyfish"].open(
        {},
        {"api_key": "secret"},
        transport=transport(lambda _: httpx2.Response(200, json={"results": []})),
        options=SearchOptions(),
    ) as web:
        result = await web.search(WebSearchRequest(query="query", limit=5))
    assert result.results == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": {}},
        {"results": [{"title": None, "url": "https://example.com/", "snippet": None}]},
        {"results": [{"url": 1}]},
    ],
)
async def test_search_rejects_malformed_responses(payload, providers) -> None:
    with pytest.raises(WebProviderError, match="web_search_response_invalid"):
        async with providers["tinyfish"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(200, json=payload)),
            options=SearchOptions(),
        ) as web:
            await web.search(WebSearchRequest(query="query", limit=1))


@pytest.mark.anyio
async def test_scrape_maps_single_fetch_and_truncates_utf8(providers, policy) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "secret"
        assert json.loads(request.content) == {
            "urls": ["https://example.com/article"],
            "format": "markdown",
        }
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/article",
                        "final_url": "https://www.example.com/article",
                        "title": "Article",
                        "text": "界界",
                    }
                ],
                "errors": [],
            },
        )

    async with providers["tinyfish"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=ScrapeOptions(max_content_bytes=4),
    ) as web:
        result = await web.scrape(scrape_request(max_content_bytes=4), policy=policy)
    assert result.content == "界"
    assert result.source_url == "https://example.com/article"
    assert result.canonical_url == "https://www.example.com/article"
    assert result.title == "Article"
    assert result.truncated


@pytest.mark.anyio
async def test_scrape_projects_one_upstream_url_failure_to_safe_failure(providers, policy) -> None:
    payload = {
        "results": [],
        "errors": [{"url": "https://example.com/article", "error": "bot_blocked", "status": 403}],
    }
    with pytest.raises(WebProviderError, match="web_scrape_failed"):
        async with providers["tinyfish"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(200, json=payload)),
            options=ScrapeOptions(),
        ) as web:
            await web.scrape(scrape_request(), policy=policy)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": [], "errors": []},
        {"results": [{"url": "https://other.example/", "text": "content"}], "errors": []},
        {"results": [{"url": "https://example.com/article", "text": "content"}], "errors": []},
        {
            "results": [{"url": "https://example.com/article", "text": "content"}],
            "errors": [{"url": "https://example.com/article", "error": "timeout"}],
        },
    ],
)
async def test_scrape_rejects_malformed_or_ambiguous_responses(payload, providers, policy) -> None:
    with pytest.raises(WebProviderError, match="web_scrape_response_invalid"):
        async with providers["tinyfish"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(200, json=payload)),
            options=ScrapeOptions(),
        ) as web:
            await web.scrape(scrape_request(), policy=policy)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "web_search_authentication_failed"), (429, "web_search_rate_limited")],
)
async def test_search_preserves_transport_failure_semantics(status, code, providers) -> None:
    with pytest.raises(WebProviderResponseError) as caught:
        async with providers["tinyfish"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(status, text="private upstream response")),
            options=SearchOptions(),
        ) as web:
            await web.search(WebSearchRequest(query="query", limit=1))
    assert caught.value.code == code
    assert "private" not in str(caught.value)


@pytest.mark.anyio
async def test_search_preserves_transport_timeout_semantics(providers) -> None:
    def timeout(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("private timeout diagnostic")

    with pytest.raises(TimeoutError):
        async with providers["tinyfish"].open(
            {},
            {"api_key": "secret"},
            transport=transport(timeout),
            options=SearchOptions(),
        ) as web:
            await web.search(WebSearchRequest(query="query", limit=1))
