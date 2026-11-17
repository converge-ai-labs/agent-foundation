from __future__ import annotations

import json
from decimal import Decimal

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
from a13n_harness.providers.web.vendors import specstream
from pydantic import ValidationError


class SpecStreamEndpointPolicy:
    async def validate(self, endpoint: str) -> str:
        assert endpoint in {specstream.SEARCH_URL, specstream.SCRAPE_URL}
        return endpoint


def transport(handler) -> WebProviderTransport:
    return WebProviderTransport(
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        endpoint_policy=SpecStreamEndpointPolicy(),
    )


def scrape_request(*, max_content_bytes: int = 1024) -> WebScrapeRequest:
    return WebScrapeRequest(
        url="https://example.com/article",
        max_content_bytes=max_content_bytes,
        deadline_seconds=30,
        max_redirects=0,
    )


def test_definition_uses_shared_empty_configuration_and_api_key_credential(providers) -> None:
    provider = providers["specstream"]
    assert provider.display_name == "SpecStream"
    assert provider.setup_url == "https://specstream.ai/workspace"
    assert provider.supports_search and provider.supports_scrape
    assert not provider.supports_restricted_scrape
    with pytest.raises(ValidationError):
        provider.configuration_model.model_validate({"endpoint": "https://example.com"})
    with pytest.raises(ValidationError):
        provider.credential_model.model_validate({"api_key": " "})
    with pytest.raises(ValueError):
        provider.parse_credential({}, None)


@pytest.mark.anyio
@pytest.mark.parametrize(("requested", "configured", "expected"), [(2, 5, 2), (5, 1, 1), (100, 100, 100)])
async def test_search_maps_request_response_domain_filters_and_usage(
    providers, requested: int, configured: int, expected: int
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == specstream.SEARCH_URL
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content) == {"q": "find sources", "num": expected, "speed": "fast"}
        return httpx2.Response(
            200,
            json={
                "data": {
                    "sources": [
                        {"title": "Other", "url": "https://other.test/", "snippet": "filtered"},
                        {"title": "Private", "url": "https://private.example.com/", "snippet": "denied"},
                        {"title": "First", "url": "https://example.com/1", "snippet": "one"},
                        {"title": None, "url": "https://example.com/2", "snippet": None},
                    ],
                    "price_usd": 0.001,
                }
            },
        )

    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=SearchOptions(
            max_results=configured,
            allow_domains=("example.com",),
            deny_domains=("private.example.com",),
        ),
    ) as web:
        result = await web.search(WebSearchRequest(query="find sources", limit=requested))
    expected_items = [
        ("First", "https://example.com/1", "one"),
        ("", "https://example.com/2", ""),
    ][:expected]
    assert [(item.title, item.url, item.snippet) for item in result.results] == expected_items
    assert len(result.usage) == 1
    assert result.usage[0].provider == "specstream"
    assert result.usage[0].product == "web_search"
    assert result.usage[0].cost == Decimal("0.001")
    assert result.usage[0].currency == "USD"


@pytest.mark.anyio
async def test_search_accepts_empty_sources_and_max_query_length(providers) -> None:
    query = "q" * specstream.MAX_QUERY_CHARS

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content) == {"q": query, "num": 5, "speed": "fast"}
        return httpx2.Response(200, json={"data": {"sources": [], "price_usd": 0}})

    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=SearchOptions(),
    ) as web:
        result = await web.search(WebSearchRequest(query=query, limit=5))
    assert result.results == ()
    assert result.usage[0].cost == Decimal("0")


@pytest.mark.anyio
async def test_search_rejects_query_above_limit_without_dispatch(providers) -> None:
    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(lambda _: pytest.fail("invalid request dispatched")),
    ) as web:
        with pytest.raises(WebProviderError, match=r"^web_search_request_invalid$"):
            await web.search(WebSearchRequest(query="q" * (specstream.MAX_QUERY_CHARS + 1), limit=1))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"data": []},
        {"data": {}},
        {"data": {"sources": [{"url": "file:///private", "title": "Bad", "snippet": ""}]}},
        {"data": {"sources": [{"url": "https://example.com/", "title": 1, "snippet": "ok"}]}},
        {"data": {"sources": [{"url": "https://example.com/", "title": "ok", "snippet": False}]}},
    ],
)
async def test_search_rejects_malformed_responses(payload: object, providers) -> None:
    with pytest.raises(WebProviderError, match=r"^web_search_response_invalid$"):
        async with providers["specstream"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(200, json=payload)),
            options=SearchOptions(),
        ) as web:
            await web.search(WebSearchRequest(query="query", limit=1))


@pytest.mark.anyio
async def test_scrape_maps_markdown_truncates_utf8_and_records_usage(providers, policy) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == specstream.SCRAPE_URL
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content) == {"urls": ["https://example.com/article"], "format": "markdown"}
        return httpx2.Response(
            200,
            json={
                "data": {
                    "results": [
                        {
                            "url": "https://example.com/article",
                            "status": "ok",
                            "title": "Article",
                            "text": "界界",
                        }
                    ],
                    "price_usd": 0.0008,
                }
            },
        )

    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(handle),
        options=ScrapeOptions(max_content_bytes=4),
    ) as web:
        result = await web.scrape(scrape_request(max_content_bytes=4), policy=policy)
    assert result.content == "界"
    assert result.source_url == "https://example.com/article"
    assert result.canonical_url == "https://example.com/article"
    assert result.title == "Article"
    assert result.truncated
    assert len(result.usage) == 1
    assert result.usage[0].product == "extract"
    assert result.usage[0].cost == Decimal("0.0008")


@pytest.mark.anyio
@pytest.mark.parametrize("upstream_truncated", [False, True])
@pytest.mark.parametrize("max_content_bytes", [4, 1024])
async def test_scrape_preserves_upstream_and_local_truncation(
    providers, policy, upstream_truncated: bool, max_content_bytes: int
) -> None:
    payload = {
        "data": {
            "results": [
                {
                    "url": "https://example.com/article",
                    "status": "ok",
                    "text": "界界",
                    "text_truncated": upstream_truncated,
                }
            ]
        }
    }
    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(lambda _: httpx2.Response(200, json=payload)),
    ) as web:
        result = await web.scrape(scrape_request(max_content_bytes=max_content_bytes), policy=policy)
    assert result.content == ("界" if max_content_bytes == 4 else "界界")
    assert result.truncated is (upstream_truncated or max_content_bytes == 4)


@pytest.mark.anyio
async def test_scrape_handles_empty_status_without_failing(providers, policy) -> None:
    payload = {"data": {"results": [{"url": "https://example.com/article", "status": "empty"}], "price_usd": 0}}
    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(lambda _: httpx2.Response(200, json=payload)),
        options=ScrapeOptions(),
    ) as web:
        result = await web.scrape(scrape_request(), policy=policy)
    assert result.content == ""
    assert not result.truncated


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["blocked", "not_found", "unsupported", "invalid_url", "failed"])
async def test_scrape_maps_failure_statuses(status: str, providers, policy) -> None:
    payload = {"data": {"results": [{"url": "https://example.com/article", "status": status, "text": "Error page"}]}}
    with pytest.raises(WebProviderError, match=r"^web_scrape_failed$"):
        async with providers["specstream"].open(
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
        [],
        {},
        {"data": []},
        {"data": {"results": []}},
        {"data": {"results": [{"url": "https://example.com/article", "text": "missing status"}]}},
        {"data": {"results": [{"url": "https://example.com/article", "status": "unknown", "text": "ok"}]}},
        {"data": {"results": [{"url": "https://example.com/article", "status": [], "text": "ok"}]}},
        {"data": {"results": [{"url": "https://example.com/article", "status": {}, "text": "ok"}]}},
        {"data": {"results": [{"status": "ok", "text": "ok", "text_truncated": "false"}]}},
        {"data": {"results": [{"url": "https://example.com/article", "status": "ok"}]}},
    ],
)
async def test_scrape_rejects_malformed_responses(payload: object, providers, policy) -> None:
    with pytest.raises(WebProviderError, match=r"^web_scrape_response_invalid$"):
        async with providers["specstream"].open(
            {},
            {"api_key": "secret"},
            transport=transport(lambda _: httpx2.Response(200, json=payload)),
            options=ScrapeOptions(),
        ) as web:
            await web.scrape(scrape_request(), policy=policy)


@pytest.mark.anyio
async def test_scrape_refuses_domain_restrictions_before_dispatch(providers, policy) -> None:
    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        options=ScrapeOptions(allow_domains=("example.com",)),
        transport=transport(lambda _: pytest.fail("restricted scrape dispatched")),
    ) as web:
        with pytest.raises(WebProviderError, match=r"^web_scrape_domain_restrictions_unsupported$"):
            await web.scrape(scrape_request(), policy=policy)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_rate_limit_and_timeout_semantics(providers, policy, operation: str) -> None:
    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(
            lambda _: httpx2.Response(
                503,
                headers={"Retry-After": "4"},
                json={"error": {"code": "rate_limited", "message": "private diagnostic secret"}},
            )
        ),
    ) as web:
        with pytest.raises(WebProviderResponseError, match=f"^web_{operation}_unavailable$") as raised:
            if operation == "search":
                await web.search(WebSearchRequest(query="query", limit=1))
            else:
                await web.scrape(scrape_request(), policy=policy)
        assert raised.value.retry_after_seconds == 4
        assert "private diagnostic secret" not in str(raised.value)

    def timeout(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("private timeout diagnostic")

    async with providers["specstream"].open(
        {},
        {"api_key": "secret"},
        transport=transport(timeout),
    ) as web:
        with pytest.raises(TimeoutError):
            if operation == "search":
                await web.search(WebSearchRequest(query="query", limit=1))
            else:
                await web.scrape(scrape_request(), policy=policy)
