from contextlib import asynccontextmanager
from dataclasses import replace

import anyio
import httpx2
import pytest
from a13n_harness.providers.connector.builtins import COMPOSIO

pytestmark = pytest.mark.anyio


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
