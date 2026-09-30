"""Provider HTTP ownership is independent of Agent orchestration."""

import httpx2
import pytest
from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.web import WebProviderError, WebProviderTransport, WebSearchRequest
from a13n_harness.providers.web.builtins import built_in_web_providers
from anyio import fail_after, sleep_forever


class EndpointPolicy:
    async def validate(self, endpoint: str) -> str:
        assert endpoint == "https://api.search.brave.com/res/v1/web/search"
        return endpoint


class Client(httpx2.AsyncClient):
    def __init__(self, handler, *, cleanup: str = "normal"):
        super().__init__(transport=httpx2.MockTransport(handler))
        self.closes = 0
        self.cleanup = cleanup

    async def aclose(self) -> None:
        self.closes += 1
        await super().aclose()
        if self.cleanup == "hang":
            await sleep_forever()
        if self.cleanup == "fail":
            raise RuntimeError("private secret diagnostic")


def brave():
    return next(provider for provider in built_in_web_providers() if provider.type == "brave")


@pytest.mark.anyio
@pytest.mark.parametrize("borrowed", [False, True])
async def test_client_ownership_on_success_and_invalid_response(borrowed):
    client = Client(lambda request: httpx2.Response(200, json={"web": {"results": []}}))
    transport = WebProviderTransport(
        client=client if borrowed else None, client_factory=lambda: client, endpoint_policy=EndpointPolicy()
    )
    async with brave().open({}, {"api_key": "test-key"}, transport=transport) as provider:
        assert (await provider.search(WebSearchRequest(query="query", limit=1))).results == ()
    assert client.closes == (0 if borrowed else 1)
    if borrowed:
        assert not client.is_closed
        await client.aclose()

    client = Client(lambda request: httpx2.Response(200, content=b"not json"))
    transport = WebProviderTransport(
        client=client if borrowed else None, client_factory=lambda: client, endpoint_policy=EndpointPolicy()
    )
    async with brave().open({}, {"api_key": "test-key"}, transport=transport) as provider:
        with pytest.raises(WebProviderError, match="web_search_response_invalid"):
            await provider.search(WebSearchRequest(query="query", limit=1))
    assert client.closes == (0 if borrowed else 1)
    if borrowed:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("cleanup", ["normal", "hang", "fail"])
async def test_cancellation_preserved_and_cleanup_bounded(cleanup, caplog, monkeypatch):
    # Shrink the shielded close bound so the hanging cleanup is cut short quickly.
    monkeypatch.setattr("a13n_harness.providers.web.transport._CLOSE_TIMEOUT_SECONDS", 0.01)

    async def handler(request):
        await sleep_forever()

    client = Client(handler, cleanup=cleanup)
    transport = WebProviderTransport(client_factory=lambda: client, endpoint_policy=EndpointPolicy())
    with pytest.raises(TimeoutError), fail_after(0.01):
        async with brave().open({}, {"api_key": "test-key"}, transport=transport) as provider:
            await provider.search(WebSearchRequest(query="query", limit=1))
    assert client.closes == 1
    assert "private secret diagnostic" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("url", "borrowed"),
    [
        ("http://127.0.0.1/private", False),
        ("https://127.0.0.1/private", False),
        ("https://[::1]/private", False),
        ("https://user:synthetic-secret@allowed.example/api", False),
        # Client ownership does not depend on the rejected URL; one borrowed case suffices.
        ("https://allowed.example/api#fragment", True),
    ],
)
async def test_actual_request_destination_is_checked_before_send(url, borrowed):
    from a13n_harness import RunConfiguration
    from a13n_harness.providers.endpoint_policy import EndpointPolicy as PublicEndpointPolicy

    sent = []
    client = Client(lambda request: sent.append(request) or httpx2.Response(200, text="unexpected"))
    transport = WebProviderTransport(
        client=client if borrowed else None,
        client_factory=lambda: client,
        endpoint_policy=PublicEndpointPolicy(configuration=RunConfiguration(allowed_hosts={"allowed.example"})),
    )
    with pytest.raises(WebProviderError, match="web_search_failed") as error:
        await transport.exchange(
            lambda http: http.build_request("GET", url),
            operation="search",
            max_response_bytes=1024,
        )
    assert not sent
    assert "synthetic-secret" not in str(error.value)
    assert client.closes == (0 if borrowed else 1)
    if borrowed:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url,checked",
    [
        ("https://serpapi.com/search?api_key=synthetic-secret&q=query", "https://serpapi.com/search"),
        (
            "https://r.jina.ai/https://example.com/article?key=synthetic-secret",
            "https://r.jina.ai/https://example.com/article",
        ),
    ],
)
async def test_generated_query_credentials_and_jina_paths_retain_the_actual_destination(url, checked):
    from a13n_harness.providers.endpoint_policy import EndpointPolicy as PublicEndpointPolicy
    from a13n_harness.providers.endpoint_policy import EndpointPolicyError

    validated = []
    sent = []

    class Policy(PublicEndpointPolicy):
        async def validate(self, endpoint):
            validated.append(endpoint)
            return await super().validate(endpoint)

    # The configurable endpoint contract still rejects credential-bearing queries.
    with pytest.raises(EndpointPolicyError, match="sensitive"):
        PublicEndpointPolicy().validate_syntax(url)
    client = Client(lambda outgoing: sent.append(str(outgoing.url)) or httpx2.Response(200, text="ok"))
    transport = WebProviderTransport(client=client, endpoint_policy=Policy())
    try:
        assert (
            await transport.exchange(
                lambda http: http.build_request("GET", url),
                operation="search",
                max_response_bytes=1024,
            )
            == b"ok"
        )
        assert validated == [checked]
        assert sent == [url]
        assert not client.is_closed
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_scrape_deadline_cleans_up_its_owned_http_client(policy):
    from a13n_harness.providers.web import WebProviderDefinition, WebScrapeRequest
    from a13n_harness.providers.web.configuration import EmptyConfiguration

    async def handler(request):
        await sleep_forever()

    async def scrape(configuration, credential, request, options, transport, callback_policy):
        await transport.exchange(
            lambda http: http.build_request("GET", "https://api.search.brave.com/res/v1/web/search"),
            operation="scrape",
            max_response_bytes=1024,
        )
        raise AssertionError("deadline should interrupt the request")

    client = Client(handler)
    transport = WebProviderTransport(client_factory=lambda: client, endpoint_policy=EndpointPolicy())
    provider = WebProviderDefinition(
        type="custom",
        display_name="Custom",
        authentication=Authentication(mode=CredentialMode.forbidden),
        configuration_model=EmptyConfiguration,
        credential_model=EmptyConfiguration,
        setup_url="https://example.com/setup",
        scrape=scrape,
    )
    with pytest.raises(TimeoutError):
        async with provider.open({}, None, transport=transport) as web:
            await web.scrape(
                WebScrapeRequest(
                    url="https://example.com/article",
                    max_content_bytes=4,
                    deadline_seconds=0.01,
                    max_redirects=0,
                ),
                policy=policy,
            )
    assert client.closes == 1
