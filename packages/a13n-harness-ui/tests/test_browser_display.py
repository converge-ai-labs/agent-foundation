from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui import browser_display

pytestmark = pytest.mark.anyio


def connection(chunks):
    reader = AsyncMock()
    reader.read.side_effect = chunks
    writer = Mock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


async def test_wayvnc_handoff_waits_for_native_client_destruction(tmp_path, monkeypatch):
    busy = connection([b'{"code":0,"data":[{"id":"3"}]}'])
    ready = connection([b'{"code":', b'0,"data":[]}'])
    connect = AsyncMock(side_effect=[busy, ready])
    monkeypatch.setattr(browser_display.asyncio, "open_unix_connection", connect)
    await browser_display.settle_wayvnc(tmp_path / "control.sock")
    assert connect.await_count == 2
    for _, writer in (busy, ready):
        writer.write.assert_called_once_with(b'{"method":"client-list","id":1}')
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("chunks", [[b""], [b'{"code":1}'], [b'{"code":0,"data":{}}'], [b"x" * 65537]])
async def test_wayvnc_cleanup_errors_fail_closed_and_close_the_socket(tmp_path, monkeypatch, chunks):
    _, writer = pair = connection(chunks)
    monkeypatch.setattr(browser_display.asyncio, "open_unix_connection", AsyncMock(return_value=pair))
    with pytest.raises(RuntimeError):
        await browser_display.settle_wayvnc(tmp_path / "control.sock")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


async def test_wayvnc_unresponsive_control_socket_is_bounded(tmp_path, monkeypatch):
    reader, writer = connection([])

    async def blocked(_):
        await asyncio.Event().wait()

    reader.read.side_effect = blocked
    monkeypatch.setattr(browser_display.asyncio, "open_unix_connection", AsyncMock(return_value=(reader, writer)))
    timeout = asyncio.timeout
    monkeypatch.setattr(browser_display.asyncio, "timeout", lambda _: timeout(0.02))
    with pytest.raises(TimeoutError):
        await browser_display.settle_wayvnc(tmp_path / "control.sock")
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


async def test_sway_ipc_reads_fragmented_native_window_tree_and_closes_transport(monkeypatch):
    import json
    import struct

    tree = {"nodes": [{"id": 5, "app_id": "chromium-browser", "name": "Browser window"}], "floating_nodes": []}
    payload = json.dumps(tree).encode()
    reader = AsyncMock()
    reader.readexactly.side_effect = [b"i3-ipc" + struct.pack("<II", len(payload), 4), payload]
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
    monkeypatch.setattr(browser_display.asyncio, "open_unix_connection", AsyncMock(return_value=(reader, writer)))
    assert await browser_display.sway_windows({"SWAYSOCK": "/private/sway.sock"}) == {5: "Browser window"}
    writer.write.assert_called_once_with(b"i3-ipc" + struct.pack("<II", 0, 4))
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("response", [[], [{"success": False}], {}, [{"success": True}, {"error": "missing window"}]])
async def test_sway_focus_fails_closed_on_native_command_rejection(monkeypatch, response):
    monkeypatch.setattr(browser_display, "sway_request", AsyncMock(return_value=response))
    with pytest.raises(RuntimeError, match="compositor"):
        await browser_display.sway_command({"SWAYSOCK": "/private/sway.sock"}, 42, "focus")
    browser_display.sway_request.assert_awaited_once_with({"SWAYSOCK": "/private/sway.sock"}, 0, b"[con_id=42] focus")
