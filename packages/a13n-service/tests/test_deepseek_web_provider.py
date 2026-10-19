"""The built-in DeepSeek Web definition reaches Service discovery and attempt backends."""

import base64
import json

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.web import ScrapeOptions, SearchOptions, WebScrapeRequest, WebSearchRequest
from a13n_service.distribution import OSS
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider, list_provider_types
from a13n_service.resources.web_providers import runtime as web
from pydantic import SecretStr


@pytest.mark.anyio
async def test_deepseek_discovery_and_encrypted_credential_execution(monkeypatch) -> None:
    registry = Registry.of(OSS.providers)
    described = next(item for item in list_provider_types(registry, "web").items if item.type == "deepseek")
    assert described.display_name == "DeepSeek"
    assert described.operations == ["search", "scrape"]
    assert described.setup_url == "https://platform.deepseek.com/api_keys"
    assert described.credential_schema["required"] == ["api_key"]
    assert described.authentication.mode == "required"

    keys = KeyRing(active_key_id="test", keys={"test": SecretStr(base64.b64encode(b"k" * 32).decode())})
    location = SecretLocation("org_test", "web_providers", "credential", "wprov_deepseek")
    provider = ResolvedProvider(
        id="wprov_deepseek",
        version=1,
        type="deepseek",
        config={},
        enabled=True,
        credential=keys.protect(b'{"api_key":"deepseek-test-key"}', location),
        extra_headers={},
        location=location,
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer deepseek-test-key"
        if request.url.path == "/v1/tools/search":
            return httpx2.Response(
                200,
                json={"results": [{"title": "Page", "url": "https://example.com/", "summary": "Summary"}]},
            )
        assert request.url.path == "/v1/tools/fetch"
        assert json.loads(request.content) == {"url": "https://example.com/"}
        return httpx2.Response(200, json={"title": "Page", "content": "# Page"})

    clients: list[httpx2.AsyncClient] = []

    def client() -> httpx2.AsyncClient:
        opened = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        clients.append(opened)
        return opened

    monkeypatch.setattr(web, "provider_client", client)
    context = {"registry": registry, "keys": keys, "policy": EndpointPolicy()}
    async with web.open_search_backend(provider, SearchOptions(), **context) as binding:
        assert binding.backend_id == "wprov_deepseek"
        result = await binding.provider.search(WebSearchRequest(query="Page", limit=1))
        assert result.results[0].snippet == "Summary"
    async with web.open_scrape_backend(provider, ScrapeOptions(), **context) as binding:
        assert binding.backend_id == "wprov_deepseek"
        result = await binding.provider.scrape(
            WebScrapeRequest(url="https://example.com/", max_content_bytes=1024, deadline_seconds=30, max_redirects=0)
        )
        assert result.content == "# Page"
    assert len(clients) == 2 and all(opened.is_closed for opened in clients)
