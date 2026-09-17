from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.capabilities.web import WebProviderError, WebScrapeRequest, WebSearchRequest
from a13n_service.provider_plugins import WebProviderResponseError
from a13n_service.web.adapters import WebProviderTransport
from a13n_service.web.domain import (
    MAX_SCRAPE_CONTENT_BYTES,
    CreateWebProviderRequest,
    ScrapeSelection,
    SearchSelection,
    UpdateWebProviderRequest,
)
from a13n_service.web.providers.common import scrape_response_bytes
from pydantic import ValidationError

PROVIDER_ID = "wprov_1234567890abcdef"


class NoDNSPolicy:
    async def validate(self, endpoint: str) -> str:
        assert endpoint in {
            "https://api.search.brave.com/res/v1/web/search",
            "https://api.exa.ai/search",
            "https://api.exa.ai/contents",
        }
        return endpoint


def transport(handler) -> WebProviderTransport:
    return WebProviderTransport(
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        endpoint_policy=NoDNSPolicy(),
    )


@pytest.mark.parametrize(
    "domain",
    [
        "https://example.com",
        "example.com/path",
        "example.com:443",
        "*.example.com",
        "127.0.0.1",
        "[::1]",
        "bad_name.com",
        "",
        " example.com",
        "example..com",
    ],
)
def test_rejects_non_dns_filters(domain: str) -> None:
    with pytest.raises(ValidationError):
        SearchSelection(provider_id=PROVIDER_ID, allow_domains=(domain,))


def test_domain_and_credential_normalization_boundaries() -> None:
    assert SearchSelection(provider_id=PROVIDER_ID, allow_domains=("EXAMPLE.COM.", "例子.中国")).allow_domains == (
        "example.com",
        "xn--fsqu00a.xn--fiqs8s",
    )
    assert SearchSelection(
        provider_id=PROVIDER_ID,
        allow_domains=("Example.com", "example.com."),
    ).allow_domains == ("example.com",)
    request = CreateWebProviderRequest(type="brave", name="  Work  ", credential={"api_key": " key "})
    assert request.name == "Work"
    assert request.credential == {"api_key": " key "}
    from a13n_service.web.registry import built_in_web_provider_registry

    registry = built_in_web_provider_registry()
    for key in ("", "   ", "é" * 2049):
        with pytest.raises(ValidationError):
            registry.validate_credentials("exa", {"api_key": key})
    with pytest.raises(ValidationError):
        UpdateWebProviderRequest(credential=None)
    with pytest.raises(ValidationError):
        registry.validate_configuration("exa", {"endpoint": "https://example.com"})


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", ["brave", "exa"])
async def test_request_destination_credentials_limits_and_result_filter(provider_type: str) -> None:
    calls = []

    def handle(request):
        calls.append(request)
        if provider_type == "brave":
            assert request.headers["x-subscription-token"] == "secret-key"
            assert request.url.params["count"] == "2"
            assert request.url.params["q"] == "find sources"
        else:
            assert request.headers["x-api-key"] == "secret-key"
            body = json.loads(request.content)
            assert body["numResults"] == 2
            assert body["includeDomains"] == ["example.com"]
            assert "summary" not in body["contents"]
        results = [
            {"url": "https://example.com.evil.test/a", "title": "excluded"},
            {
                "url": "https://sub.example.com/b",
                "title": "included",
                "description": "snippet",
                "highlights": ["snippet"],
            },
        ]
        return httpx2.Response(
            200, json={"web": {"results": results}} if provider_type == "brave" else {"results": results}
        )

    result = await transport(handle).search(
        provider_type,
        "secret-key",
        WebSearchRequest(query="find sources", limit=10),
        SearchSelection(provider_id=PROVIDER_ID, max_results=2, allow_domains=("example.com",)),
    )
    assert [(item.title, item.url, item.snippet) for item in result.results] == [
        ("included", "https://sub.example.com/b", "snippet")
    ]
    assert result.usage == ()
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "web_search_authentication_failed"),
        (402, "web_search_quota_exceeded"),
        (429, "web_search_rate_limited"),
        (503, "web_search_unavailable"),
        (302, "web_search_failed"),
        (422, "web_search_request_invalid"),
    ],
)
async def test_failure_mapping_rejects_redirects_without_echoing_content(status, code) -> None:
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(
            status,
            headers={"Location": "https://other.example/", "Retry-After": "45"},
            text="secret upstream diagnostic",
        )

    with pytest.raises(WebProviderResponseError) as caught:
        await transport(handle).search(
            "exa", "secret-key", WebSearchRequest(query="query", limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )
    assert caught.value.code == code
    assert caught.value.retry_after == 45
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.anyio
async def test_scrape_failure_mapping_retains_operation_and_retry_context() -> None:
    def handle(_request):
        return httpx2.Response(429, headers={"Retry-After": "7"}, text="private diagnostic")

    with pytest.raises(WebProviderResponseError) as caught:
        await transport(handle).scrape(
            "exa",
            "key",
            WebScrapeRequest(
                url="https://example.com/article",
                max_content_bytes=1024,
                deadline_seconds=30,
                max_redirects=0,
            ),
            ScrapeSelection(provider_id=PROVIDER_ID),
        )

    assert caught.value.code == "web_scrape_rate_limited"
    assert caught.value.retry_after == 7
    assert "private diagnostic" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"not json", id="invalid-json"),
        pytest.param(b"{}", id="missing-results"),
        pytest.param(b'{"results":[{"url":"file:///secret"}]}', id="invalid-result-url"),
        pytest.param(b" " * (1024 * 1024 + 1), id="oversized-response"),
    ],
)
async def test_invalid_and_oversized_responses_fail(content) -> None:
    with pytest.raises(WebProviderError, match="web_search_response_invalid"):
        await transport(lambda _: httpx2.Response(200, content=content)).search(
            "exa", "key", WebSearchRequest(query="query", limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )


@pytest.mark.anyio
async def test_brave_query_limit_does_not_dispatch_or_truncate() -> None:
    def unexpected(_):
        raise AssertionError("request must not be sent")

    with pytest.raises(WebProviderError, match="web_search_request_invalid"):
        await transport(unexpected).search(
            "brave", "key", WebSearchRequest(query="x" * 601, limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )


@pytest.mark.anyio
async def test_exa_scrape_is_single_url_bounded_and_normalized() -> None:
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url == "https://api.exa.ai/contents"
        assert request.headers["x-api-key"] == "secret-key"
        assert json.loads(request.content) == {
            "urls": ["https://example.com/article"],
            "text": {"maxCharacters": 5},
        }
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.example.com/article",
                        "title": "Article",
                        "text": "abcde",
                    }
                ]
            },
        )

    result = await transport(handle).scrape(
        "exa",
        "secret-key",
        WebScrapeRequest(
            url="https://example.com/article",
            max_content_bytes=4,
            deadline_seconds=30,
            max_redirects=0,
        ),
        ScrapeSelection(provider_id=PROVIDER_ID),
    )
    assert result.content == "abcd"
    assert result.source_url == "https://example.com/article"
    assert result.canonical_url == "https://www.example.com/article"
    assert result.title == "Article" and result.truncated
    assert len(calls) == 1


@pytest.mark.anyio
async def test_exa_scrape_truncates_multibyte_content_on_utf8_boundary() -> None:
    def handle(request):
        assert json.loads(request.content)["text"] == {"maxCharacters": 5}
        return httpx2.Response(
            200,
            json={"results": [{"url": "https://example.com/article", "text": "界界"}]},
        )

    result = await transport(handle).scrape(
        "exa",
        "key",
        WebScrapeRequest(
            url="https://example.com/article",
            max_content_bytes=4,
            deadline_seconds=30,
            max_redirects=0,
        ),
        ScrapeSelection(provider_id=PROVIDER_ID),
    )

    assert result.content == "界"
    assert len(result.content.encode("utf-8")) == 3
    assert result.truncated


@pytest.mark.anyio
async def test_exa_scrape_locally_bounds_long_text_when_provider_exceeds_requested_excerpt() -> None:
    def handle(request):
        assert json.loads(request.content)["text"] == {"maxCharacters": 17}
        return httpx2.Response(
            200,
            json={"results": [{"url": "https://example.com/article", "text": "x" * 50_000}]},
        )

    result = await transport(handle).scrape(
        "exa",
        "key",
        WebScrapeRequest(
            url="https://example.com/article",
            max_content_bytes=16,
            deadline_seconds=30,
            max_redirects=0,
        ),
        ScrapeSelection(provider_id=PROVIDER_ID),
    )

    assert result.content == "x" * 16
    assert result.truncated


@pytest.mark.anyio
async def test_exa_scrape_maximum_output_budget_is_reachable() -> None:
    content = "x" * MAX_SCRAPE_CONTENT_BYTES

    def handle(request):
        assert json.loads(request.content)["text"] == {"maxCharacters": MAX_SCRAPE_CONTENT_BYTES + 1}
        return httpx2.Response(
            200,
            json={"results": [{"url": "https://example.com/article", "text": content}]},
        )

    result = await transport(handle).scrape(
        "exa",
        "key",
        WebScrapeRequest(
            url="https://example.com/article",
            max_content_bytes=MAX_SCRAPE_CONTENT_BYTES,
            deadline_seconds=30,
            max_redirects=0,
        ),
        ScrapeSelection(provider_id=PROVIDER_ID, max_content_bytes=MAX_SCRAPE_CONTENT_BYTES),
    )

    assert len(result.content.encode("utf-8")) == MAX_SCRAPE_CONTENT_BYTES
    assert not result.truncated


@pytest.mark.anyio
async def test_exa_scrape_rejects_wire_response_beyond_escaped_json_budget() -> None:
    wire_limit = scrape_response_bytes(MAX_SCRAPE_CONTENT_BYTES)

    with pytest.raises(WebProviderError, match="web_scrape_response_invalid"):
        await transport(lambda _: httpx2.Response(200, content=b" " * (wire_limit + 1))).scrape(
            "exa",
            "key",
            WebScrapeRequest(
                url="https://example.com/article",
                max_content_bytes=MAX_SCRAPE_CONTENT_BYTES,
                deadline_seconds=30,
                max_redirects=0,
            ),
            ScrapeSelection(provider_id=PROVIDER_ID, max_content_bytes=MAX_SCRAPE_CONTENT_BYTES),
        )


@pytest.mark.anyio
async def test_brave_and_restricted_exa_scrape_are_rejected_before_dispatch() -> None:
    def unexpected(_request):
        raise AssertionError("request must not be sent")

    request = WebScrapeRequest(
        url="https://example.com/article",
        max_content_bytes=1024,
        deadline_seconds=30,
        max_redirects=0,
    )
    with pytest.raises(WebProviderError, match="web_scrape_unavailable"):
        await transport(unexpected).scrape(
            "brave",
            "key",
            request,
            ScrapeSelection(provider_id=PROVIDER_ID),
        )
    with pytest.raises(WebProviderError, match="web_scrape_domain_restrictions_unsupported"):
        await transport(unexpected).scrape(
            "exa",
            "key",
            request,
            ScrapeSelection(provider_id=PROVIDER_ID, allow_domains=("example.com",)),
        )
