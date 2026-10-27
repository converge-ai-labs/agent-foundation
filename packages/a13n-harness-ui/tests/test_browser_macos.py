from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui.browser_macos import PASSWORD_ENV, MacDesktop, password_ready, screen_sharing_available

pytestmark = pytest.mark.anyio


async def test_native_desktop_keeps_credentials_host_side_and_owns_no_service(monkeypatch):
    import a13n_harness_ui.browser_macos as module

    monkeypatch.setenv(PASSWORD_ENV, "vnc-test")
    desktop = MacDesktop()
    assert PASSWORD_ENV not in desktop.env
    assert desktop.password == desktop.readonly_password == ""
    assert not desktop.processes and not desktop.resize_available
    assert desktop.chromium_arguments == () and password_ready()
    connection = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(module, "screen_sharing", connect)
    await desktop.start()
    connect.assert_awaited_once_with("vnc-test", controlling=False)
    connection.close.assert_called_once()
    connection.wait_closed.assert_awaited_once()
    await desktop.close()
    assert desktop._password == ""


async def test_preparation_probes_banner_without_authentication(monkeypatch):
    reader = asyncio.StreamReader()
    reader.feed_data(b"RFB 003.889\n")
    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    assert await screen_sharing_available()
    connect.assert_awaited_once_with("127.0.0.1", 5900)
    writer.write.assert_not_called()
    writer.close.assert_called_once()


@pytest.mark.parametrize("text", [None, "中文 🚀\n$(not a shell command)"])
async def test_native_clipboard_uses_utf8_pipes(monkeypatch, text):
    content = "中文 🚀\n$(not a shell command)"
    reader = asyncio.StreamReader()
    reader.feed_data(content.encode())
    reader.feed_eof()
    process = Mock(stdout=reader, returncode=0, communicate=AsyncMock(), wait=AsyncMock())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv(PASSWORD_ENV, "secret")
    assert await MacDesktop().clipboard(text) == content
    assert spawn.call_args.args == ("/usr/bin/pbpaste" if text is None else "/usr/bin/pbcopy",)
    assert spawn.call_args.kwargs["env"]["LC_ALL"] == "en_US.UTF-8"
    assert PASSWORD_ENV not in spawn.call_args.kwargs["env"]
    if text is not None:
        process.communicate.assert_awaited_once_with(text.encode())
    process.kill.assert_not_called()


async def test_native_clipboard_cancellation_reaps_process(monkeypatch):
    entered = asyncio.Event()

    async def communicate(data):
        entered.set()
        await asyncio.Event().wait()

    process = Mock(returncode=None, communicate=AsyncMock(side_effect=communicate), wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    transfer = asyncio.create_task(MacDesktop().clipboard("pending"))
    await entered.wait()
    transfer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await transfer
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_native_clipboard_rejects_oversize_before_waiting_for_exit(monkeypatch):
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * 262145)
    process = Mock(stdout=reader, returncode=None, wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(OSError, match="256 KiB"):
        await MacDesktop().clipboard(None)
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
