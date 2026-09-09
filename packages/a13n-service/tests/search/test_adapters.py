from __future__ import annotations

import json

import httpx2
import pytest
from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest
from a13n_service.search.adapters import SearchResponseError, SearchTransport
from a13n_service.search.domain import CreateSearchProviderRequest, SearchSelection, UpdateSearchProviderRequest
from pydantic import ValidationError

PROVIDER_ID = "sprov_1234567890abcdef"


class NoDNSPolicy:
    async def validate(self, endpoint: str) -> str:
        assert endpoint in {"https://api.search.brave.com/res/v1/web/search", "https://api.exa.ai/search"}
        return endpoint


def transport(handler) -> SearchTransport:
    return SearchTransport(
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
        SearchSelection(provider_id=PROVIDER_ID, include_domains=(domain,))


def test_domain_and_credential_normalization_boundaries() -> None:
    assert SearchSelection(provider_id=PROVIDER_ID, include_domains=("EXAMPLE.COM.", "例子.中国")).include_domains == (
        "example.com",
        "xn--fsqu00a.xn--fiqs8s",
    )
    with pytest.raises(ValidationError):
        SearchSelection(provider_id=PROVIDER_ID, include_domains=("Example.com", "example.com."))
    request = CreateSearchProviderRequest(type="brave", name="  Work  ", credential=" key ")
    assert request.name == "Work"
    assert request.credential.get_secret_value() == " key "
    for key in ("", "   ", "é" * 2049):
        with pytest.raises(ValidationError):
            CreateSearchProviderRequest(type="exa", name="X", credential=key, enabled=False)
    with pytest.raises(ValidationError):
        UpdateSearchProviderRequest(credential=None)
    with pytest.raises(ValidationError):
        CreateSearchProviderRequest(
            type="exa", name="X", credential="key", configuration={"endpoint": "https://example.com"}
        )


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

    result = await transport(handle).dispatch(
        provider_type,
        "secret-key",
        WebSearchRequest(query="find sources", limit=10),
        SearchSelection(provider_id=PROVIDER_ID, max_results=2, include_domains=("example.com",)),
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

    with pytest.raises(SearchResponseError) as caught:
        await transport(handle).dispatch(
            "exa", "secret-key", WebSearchRequest(query="query", limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )
    assert caught.value.code == code
    assert caught.value.retry_after == 45
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content", [b"not json", b"{}", b'{"results":[{"url":"file:///secret"}]}', b" " * (1024 * 1024 + 1)]
)
async def test_invalid_and_oversized_responses_fail(content) -> None:
    with pytest.raises(WebProviderError, match="web_search_response_invalid"):
        await transport(lambda _: httpx2.Response(200, content=content)).dispatch(
            "exa", "key", WebSearchRequest(query="query", limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )


@pytest.mark.anyio
async def test_brave_query_limit_does_not_dispatch_or_truncate() -> None:
    def unexpected(_):
        raise AssertionError("request must not be sent")

    with pytest.raises(WebProviderError, match="web_search_request_invalid"):
        await transport(unexpected).dispatch(
            "brave", "key", WebSearchRequest(query="x" * 601, limit=1), SearchSelection(provider_id=PROVIDER_ID)
        )
