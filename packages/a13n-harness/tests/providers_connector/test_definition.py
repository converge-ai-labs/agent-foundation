from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import anyio
import httpx2
import pytest
from a13n_harness.providers.connector import ConnectorProviderCatalog
from a13n_harness.providers.connector.builtins import COMPOSIO
from a13n_harness.providers.plugins import ProviderManifest, load_provider_plugins

pytestmark = pytest.mark.anyio


def test_manifest_metadata_is_explicit_immutable_and_inert(monkeypatch):
    calls = []
    manifest = ProviderManifest(api_version=1, connector=(COMPOSIO,))
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: pytest.fail("metadata opened client"))

    def entries(*, group):
        assert group == "a13n_harness.providers.plugins"
        return [
            SimpleNamespace(
                name="chosen", value="fixture:manifest", dist=None, load=lambda: calls.append("load") or manifest
            ),
            SimpleNamespace(
                name="other", value="fixture:other", dist=None, load=lambda: pytest.fail("unselected import")
            ),
        ]

    monkeypatch.setattr("a13n_harness.providers.plugins.importlib.metadata.entry_points", entries)
    assert load_provider_plugins(()) == ()
    assert calls == []
    catalog = ConnectorProviderCatalog(load_provider_plugins(("chosen",))[0].manifest.connector)
    assert catalog["composio"] is COMPOSIO
    with pytest.raises(TypeError):
        catalog["other"] = COMPOSIO
    with pytest.raises(ValueError, match="duplicate"):
        ProviderManifest(api_version=1, connector=(COMPOSIO, COMPOSIO))
    with pytest.raises(ValueError, match="duplicate"):
        ConnectorProviderCatalog((COMPOSIO, COMPOSIO))


@pytest.mark.parametrize("cancel", [False, True])
async def test_definition_composes_structured_scopes_and_closes_owned_transport(monkeypatch, cancel):
    completed = anyio.Event()
    calls = 0

    class Transport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            pytest.fail("No network operation expected")

        async def aclose(self):
            nonlocal calls
            calls += 1
            await anyio.sleep(0)
            completed.set()

    client = httpx2.AsyncClient(transport=Transport())
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: client)

    @asynccontextmanager
    async def open_provider(configuration, credential, http):
        async with anyio.create_task_group():
            yield object()

    definition = replace(COMPOSIO, open_provider=open_provider)
    with anyio.CancelScope() as scope:
        async with definition.open({}, {"api_key": "test"}):
            if cancel:
                scope.cancel()
                await anyio.sleep_forever()
    assert scope.cancelled_caught is cancel
    assert completed.is_set() and calls == 1
