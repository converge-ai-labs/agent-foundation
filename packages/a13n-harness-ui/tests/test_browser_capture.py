from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui.browser_capture import MAX_FRAME, WindowPreview
from a13n_harness_ui.errors import HarnessUiError

pytestmark = pytest.mark.anyio


def process():
    return Mock(
        stdout=asyncio.StreamReader(),
        stderr=asyncio.StreamReader(),
        returncode=None,
        communicate=AsyncMock(return_value=(b"", b"")),
    )


async def test_frames_are_bounded_latest_only_and_revocation_discards_pixels():
    child = process()
    preview = WindowPreview(child)
    try:
        child.stdout.feed_data(struct.pack("!I", 3) + b"one" + struct.pack("!I", 3) + b"two")
        await asyncio.sleep(0)
        assert preview.frames.qsize() == 1
        assert await preview.receive() == b"two"
        preview.publish(b"stale")
        preview.close()
        with pytest.raises(HarnessUiError):
            await preview.receive()
        child.terminate.assert_called_once()
    finally:
        await preview.wait_closed()
    child.communicate.assert_awaited_once()


@pytest.mark.parametrize("data", [b"", struct.pack("!I", MAX_FRAME + 1), struct.pack("!I", 20) + b"partial"])
async def test_worker_exit_or_invalid_frame_fails_closed(data):
    child = process()
    preview = WindowPreview(child)
    child.stdout.feed_data(data)
    child.stdout.feed_eof()
    with pytest.raises(HarnessUiError):
        await preview.receive()
    await preview.wait_closed()
    assert preview.is_closing()


async def test_cancelled_start_reaps_capture_worker(monkeypatch):
    child = process()
    spawned = asyncio.Event()

    async def spawn(*args, **kwargs):
        assert args[-2:] == ("123", "456")
        spawned.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(WindowPreview.start(123, 456))
    await spawned.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    child.terminate.assert_called_once()
    child.communicate.assert_awaited_once()


@pytest.mark.parametrize("human", [False, True])
async def test_http_ticket_preview_gateway_rejects_all_input_and_reaps(tmp_path, monkeypatch, human):
    from a13n_harness_ui.browser import Binding
    from a13n_harness_ui.browser_macos import MacDesktop
    from starlette.websockets import WebSocket

    from .test_sharing_webui import shared_server

    child = process()
    preview = WindowPreview(child)
    monkeypatch.setattr(WindowPreview, "start", AsyncMock(return_value=preview))
    async with shared_server(tmp_path, skip_auth=True, share_computer=True) as (server, app, client):
        thread = (await app.create_thread()).thread_id
        host = app.browsers()
        host.desktop = MacDesktop()
        host.process = Mock(pid=123, returncode=0)
        host.bindings[thread] = Binding(status="ready", preview_window=456)
        identity = host.state(thread).session_id
        token = (await host.control(thread, identity, human=True)).control_token if human else ""
        response = await client.post(
            f"/api/threads/{thread}/browser/access",
            json={
                "session_id": identity,
                "control_token": token,
            },
        )
        access = response.json()
        assert access["protocol"] == "jpeg" and not access["control"] and not access["password"]
        inbound, outbound = asyncio.Queue(), asyncio.Queue()
        inbound.put_nowait({"type": "websocket.connect"})
        socket = WebSocket({"type": "websocket", "subprotocols": []}, inbound.get, outbound.put)
        endpoint = next(
            route.endpoint for route in server.routes if route.path == "/api/threads/{thread_id}/browser/connect"
        )
        task = asyncio.create_task(endpoint(socket, thread, access["ticket"]))
        try:
            async with asyncio.timeout(5):
                assert (await outbound.get())["type"] == "websocket.accept"
                preview.publish(b"jpeg-frame")
                assert (await outbound.get())["bytes"] == b"jpeg-frame"
                inbound.put_nowait({"type": "websocket.receive", "bytes": b"keyboard mouse clipboard"})
                await task
                assert preview.is_closing() and not host.connections
                assert host.human_thread == (thread if human else "")
                child.communicate.assert_awaited_once()
                again = WebSocket({"type": "websocket"}, inbound.get, outbound.put)
                await endpoint(again, thread, access["ticket"])
                messages = []
                while not outbound.empty():
                    messages.append(outbound.get_nowait())
                assert messages[-1]["code"] == 4403
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await preview.wait_closed()
            host.process = None
