from __future__ import annotations

import httpx2
import pytest
from a13n_harness.capabilities.web import WebScrapeRequest, WebSearchRequest
from a13n_harness.memory import MemoryBackend
from a13n_harness.memory_plugins import MemoryBackendCatalog
from a13n_service.app import create_app
from a13n_service.models.provider_adapters.types import RuntimeProvider
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.settings import Settings
from a13n_service.web.registry import WebProviderRegistry


class AllowPolicy:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def authorize(self, url: str, *, purpose: str) -> None:
        assert purpose == "scrape"
        self.urls.append(url)


@pytest.mark.anyio
async def test_installed_entry_point_dispatches_web_and_builds_native_model_provider() -> None:
    app = create_app(Settings.model_validate({"provider_plugins": {"enabled": ["acme"]}}))
    assert app.state.settings.provider_plugins.enabled == ("acme",)
    catalogs = load_provider_catalogs(("acme",))
    assert catalogs.plugins[0].distribution_name == "a13n-provider-acme-example"

    web = WebProviderRegistry(catalogs.web)
    configuration = web.require("acme_web").configuration_model.model_validate({"index": "guides"})
    credentials = web.validate_credentials("acme_web", {"token": "test-token"})
    async with web.runtime("acme_web") as runtime:
        search = await runtime.search(
            configuration=configuration,
            credentials=credentials,
            request=WebSearchRequest(query="plugins", limit=2),
            max_results=2,
            allow_domains=(),
            deny_domains=(),
        )
        policy = AllowPolicy()
        scrape = await runtime.scrape(
            configuration=configuration,
            credentials=credentials,
            request=WebScrapeRequest(
                url="https://docs.example.com/result",
                max_content_bytes=1024,
                deadline_seconds=30,
                max_redirects=5,
            ),
            policy=policy,
            max_content_bytes=1024,
        )
    assert search.results[0].title == "guides: plugins"
    assert scrape.content == "Installed Provider package content"
    assert policy.urls == ["https://docs.example.com/result"]

    integration = next(item for item in catalogs.model if item.type == "acme_model")
    async with httpx2.AsyncClient() as client:
        provider = integration.build_provider(
            RuntimeProvider(
                type="acme_model",
                configuration={},
                endpoint="https://models.example.com/v1",
                credential="test-token",
            ),
            client,
            "openai.responses",
        )
        try:
            assert str(provider.base_url).startswith("https://models.example.com/v1")
        finally:
            await provider.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_installed_memory_plugin_reuses_harness_contract_without_opening_transport() -> None:
    from acme_provider.plugin import AcmeMemoryPlugin

    service_catalog = MemoryBackendCatalog(load_provider_catalogs(("acme",)).memory)
    plugin = service_catalog["acme.memory"]
    assert isinstance(plugin, AcmeMemoryPlugin)
    standalone_catalog = MemoryBackendCatalog((plugin,))
    assert standalone_catalog[plugin.key] is plugin
    configuration = plugin.configuration_model.model_validate({"base_url": "http://127.0.0.1:18888"})
    credential = plugin.credential_model.model_validate({"api_key": "test-token"})
    assert "test-token" not in repr(credential)
    # Opening the native adapter is inert: no server or vendor account is needed.
    async with plugin.open(configuration, credential) as backend:
        assert isinstance(backend, MemoryBackend)
    with pytest.raises(ValueError):
        plugin.configuration_model.model_validate({"base_url": "file:///tmp/memory"})
