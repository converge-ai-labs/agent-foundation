"""The CDP handshake adapter bounds attempts and never forwards/logs connection secrets."""

import asyncio
import json
import logging

import pytest
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.cdp import Chromium
from websockets.asyncio.server import serve

pytestmark = pytest.mark.anyio


async def test_direct_cdp_headers_and_secret_free_client_logs(caplog):
    requests = []

    async def handler(socket):
        requests.append(socket.request)
        async for raw in socket:
            message = json.loads(raw)
            await socket.send(json.dumps({"id": message["id"], "result": {}}))

    caplog.set_level(logging.DEBUG, logger="cdp_use.client")
    caplog.set_level(logging.DEBUG, logger="websockets.client")
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        browser = await Chromium.connect(
            f"ws://127.0.0.1:{port}/devtools/browser/id?token=url-secret",
            headers={"Authorization": "Bearer header-secret"},
            discover=False,
            timeout=1,
        )
        assert browser.connected
        await browser.close()
        assert not browser.connected
    assert len(requests) == 1
    assert requests[0].path.endswith("?token=url-secret")
    assert requests[0].headers["Authorization"] == "Bearer header-secret"
    assert "url-secret" not in caplog.text and "header-secret" not in caplog.text


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_direct_cdp_refuses_redirect_before_forwarding_secrets(status, caplog):
    redirected = []
    requests = []

    async def destination(reader, writer):
        redirected.append(await reader.read(4096))
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(destination, "127.0.0.1", 0) as target:
        target_port = target.sockets[0].getsockname()[1]

        async def redirect(reader, writer):
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                f"HTTP/1.1 {status} Redirect\r\nLocation: ws://127.0.0.1:{target_port}/?token=redirect-secret\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with await asyncio.start_server(redirect, "127.0.0.1", 0) as source:
            source_port = source.sockets[0].getsockname()[1]
            with pytest.raises(BrowserError, match="Cannot connect") as failure:
                await Chromium.connect(
                    f"ws://127.0.0.1:{source_port}/?token=url-secret",
                    headers={"Authorization": "Bearer header-secret"},
                    discover=False,
                    timeout=1,
                )
    assert len(requests) == 1 and not redirected
    assert "secret" not in str(failure.value)
    assert "url-secret" not in caplog.text and "header-secret" not in caplog.text
    assert "redirect-secret" not in caplog.text


async def test_direct_cdp_open_timeout_is_bounded():
    finished = asyncio.Event()

    async def stall(reader, writer):
        try:
            await finished.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(stall, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        try:
            async with asyncio.timeout(1):
                with pytest.raises(BrowserError, match="Cannot connect"):
                    await Chromium.connect(f"ws://127.0.0.1:{port}/?token=secret", discover=False, timeout=0.05)
        finally:
            finished.set()


async def test_direct_cdp_cancellation_closes_open_socket():
    import anyio

    disconnected = asyncio.Event()

    async def handler(socket):
        try:
            async for _ in socket:
                pass  # Keep initial discovery pending until the caller cancels.
        finally:
            disconnected.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with anyio.move_on_after(0.1) as cancelled:
            await Chromium.connect(f"ws://127.0.0.1:{port}/", discover=False, timeout=5)
        assert cancelled.cancel_called
        async with asyncio.timeout(1):
            await disconnected.wait()
