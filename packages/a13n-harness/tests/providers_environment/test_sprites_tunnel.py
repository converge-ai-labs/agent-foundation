"""Private Sprites carrier cleanup, authorization and EOF propagation over real local sockets."""

import asyncio
import json

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.sprites import http
from a13n_harness.providers.environment.sprites.shared import (
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
)
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def reference(token="private-token"):
    return http.HttpReference(
        SpritesEnvironmentConfiguration(execution="envd_http", desktop=True),
        "env-test",
        None,
        NativeRuntime(SpritesConnectionConfiguration(organization="fixture"), TokenCredential(api_key=token)),
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_tunnel_closes_upstream_and_local_listener(monkeypatch, cancel):
    opened = asyncio.Event()
    closed = asyncio.Event()

    async def peer(socket):
        assert socket.request.headers["Authorization"] == "Bearer private-token"
        assert json.loads(await socket.recv()) == {"host": "127.0.0.1", "port": 8888}
        await socket.send(json.dumps({"status": "connected"}))
        opened.set()
        try:
            async for data in socket:
                await socket.send(data)
        finally:
            closed.set()

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        def local(url, **kwargs):
            assert url.startswith("wss://api.sprites.dev/v1/sprites/a13n-") and url.endswith("/proxy")
            return connect(f"ws://127.0.0.1:{port}", **kwargs)

        monkeypatch.setattr(http, "connect", local)
        endpoint = asyncio.Future()
        finish = asyncio.Event()

        async def attachment():
            async with http.private_tunnel(reference()) as url:
                endpoint.set_result(url)
                await finish.wait()

        owner = asyncio.create_task(attachment())
        await asyncio.wait([owner, endpoint], timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if owner.done():
            await owner
        bound = int((await asyncio.wait_for(endpoint, 2)).rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", bound)
        await asyncio.wait_for(opened.wait(), 2)
        writer.write(b"payload")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(7), 2) == b"payload"
        if cancel:
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        else:
            finish.set()
            await owner
        await asyncio.wait_for(closed.wait(), 2)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", bound)


async def test_endpoint_denial_precedes_token_read_or_tunnel(monkeypatch):
    ref = reference()

    class Denied:
        async def validate(self, url):
            assert url == "https://api.sprites.dev"
            raise EndpointPolicyError("denied")

    ref.backend._endpoint_policy = Denied()
    with pytest.raises(EndpointPolicyError):
        async with http.private_tunnel(ref):
            pytest.fail("must not open a carrier")
