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


async def test_saved_password_overrides_environment_and_preserves_auth_document(tmp_path, monkeypatch):
    import json
    import stat

    from a13n_harness_ui.browser_macos import MacVncCredentials

    path = tmp_path / "auth.json"
    credentials = MacVncCredentials(path)
    monkeypatch.setenv(PASSWORD_ENV, "env-pass")
    assert await credentials.load() == "env-pass"
    await credentials.document.update(lambda document: document.update(keys={"provider": "existing"}))
    await credentials.save("saved")
    assert await MacVncCredentials(path).load() == "saved"
    assert json.loads(path.read_text())["keys"] == {"provider": "existing"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        await credentials.save("too-long-password")
    assert await credentials.load() == "saved"


async def test_password_endpoint_verifies_before_persisting_and_updates_live_desktop(tmp_path, monkeypatch):
    from a13n_harness_ui import browser as module
    from a13n_harness_ui.browser_macos import MacVncCredentials

    from .test_sharing_webui import shared_server

    monkeypatch.setattr(module, "host_platform", lambda: "macos")
    verify = AsyncMock()
    monkeypatch.setattr(MacDesktop, "start", verify)
    opened = AsyncMock()
    monkeypatch.setattr(module, "open_sharing_settings", opened)
    headers = {"Authorization": "Bearer owner-test-key"}
    async with shared_server(tmp_path, share_computer=True) as (_, app, client):
        browsers = app.browsers()
        browsers.desktop = MacDesktop("old")
        await browsers.macos_credentials.save("old")
        for invalid in ("", "too-long-password", "中文", "bad\u0000"):
            response = await client.post("/api/browser/macos/password", headers=headers, json={"password": invalid})
            assert response.status_code == 400
            if invalid:
                assert invalid not in response.text
        verify.assert_not_awaited()
        verify.side_effect = OSError("sensitive upstream error")
        response = await client.post("/api/browser/macos/password", headers=headers, json={"password": "wrong"})
        assert response.status_code == 400
        assert "wrong" not in response.text and "sensitive" not in response.text
        assert await browsers.macos_credentials.load() == "old"
        verify.side_effect = None
        response = await client.post("/api/browser/macos/password", headers=headers, json={"password": "new-pass"})
        assert response.status_code == 204 and not response.content
        assert browsers.desktop._password == "new-pass"
        assert await MacVncCredentials(tmp_path / "data" / "auth.json").load() == "new-pass"
        assert (await client.post("/api/browser/macos/settings", headers=headers)).status_code == 204
        opened.assert_awaited_once()


@pytest.mark.parametrize("platform,available", [("linux", True), ("macos", False)])
async def test_macos_setup_requires_platform_and_computer_sharing(tmp_path, monkeypatch, platform, available):
    from a13n_harness_ui import browser as module
    from a13n_harness_ui.browser_macos import MacVncPasswordInput
    from a13n_harness_ui.errors import HarnessUiError

    monkeypatch.setattr(module, "host_platform", lambda: platform)
    verify, opened = AsyncMock(), AsyncMock()
    monkeypatch.setattr(MacDesktop, "start", verify)
    monkeypatch.setattr(module, "open_sharing_settings", opened)
    browsers = module.HostBrowsers(root=tmp_path / "browsers", available=available, authorize=AsyncMock())
    with pytest.raises(HarnessUiError):
        await browsers.save_macos_password(MacVncPasswordInput.model_validate({"password": "secret"}))
    with pytest.raises(HarnessUiError):
        await browsers.open_macos_settings()
    verify.assert_not_awaited()
    opened.assert_not_awaited()
    assert not (tmp_path / "auth.json").exists()


async def test_sharing_settings_opener_uses_fixed_target(monkeypatch):
    from a13n_harness_ui.browser_macos import open_sharing_settings

    process = Mock(returncode=0, wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await open_sharing_settings()
    assert spawn.call_args.args == ("/usr/bin/open", "x-apple.systempreferences:com.apple.Sharing-Settings.extension")
    process.kill.assert_not_called()
