from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui.browser_macos import MacDesktop, open_capture_settings

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "session,available",
    [
        (None, False),
        ({"onConsole": True}, True),
        ({"onConsole": True, "CGSSessionScreenIsLocked": True}, False),
        ({"onConsole": False}, False),
    ],
)
async def test_preview_requires_an_unlocked_console_session(monkeypatch, session, available):
    import sys

    from a13n_harness_ui.browser_macos import session_available

    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        Mock(kCGSessionOnConsoleKey="onConsole", CGSessionCopyCurrentDictionary=Mock(return_value=session)),
    )
    assert session_available() is available


async def test_preview_dependency_failure_is_optional(monkeypatch):
    import sys

    from a13n_harness_ui import browser_macos as module

    monkeypatch.setattr(module, "mac_ver", lambda: ("14.0", (), ""))
    monkeypatch.setitem(sys.modules, "Quartz", None)
    assert not module.preview_available()
    monkeypatch.setitem(
        sys.modules, "Quartz", Mock(CGPreflightScreenCaptureAccess=Mock(side_effect=OSError("load failed")))
    )
    assert not module.preview_available()


async def test_native_launch_needs_no_capture_permission_or_remote_service(monkeypatch):
    connect = AsyncMock(side_effect=AssertionError("No VNC service"))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    desktop = MacDesktop()
    await desktop.start()
    await desktop.settle_input()
    await desktop.close()
    assert not desktop.processes and not desktop.resize_available
    assert desktop.chromium_arguments == ()
    assert not hasattr(desktop, "clipboard") and not hasattr(desktop, "connect")
    connect.assert_not_awaited()


@pytest.mark.parametrize("platform,available", [("linux", True), ("macos", False)])
async def test_macos_settings_require_platform_and_computer_sharing(tmp_path, monkeypatch, platform, available):
    from a13n_harness_ui import browser as module
    from a13n_harness_ui.errors import HarnessUiError

    monkeypatch.setattr(module, "host_platform", lambda: platform)
    opened = AsyncMock()
    monkeypatch.setattr(module, "open_capture_settings", opened)
    browsers = module.HostBrowsers(root=tmp_path / "browsers", available=available, authorize=AsyncMock())
    with pytest.raises(HarnessUiError):
        await browsers.open_macos_settings()
    opened.assert_not_awaited()


async def test_capture_settings_opener_uses_fixed_target(monkeypatch):
    process = Mock(returncode=0, wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await open_capture_settings()
    assert spawn.call_args.args == (
        "/usr/bin/open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    )
    process.kill.assert_not_called()


async def test_removed_password_and_clipboard_endpoints(tmp_path, monkeypatch):
    from a13n_harness_ui import browser as module

    from .test_sharing_webui import shared_server

    monkeypatch.setattr(module, "host_platform", lambda: "macos")
    opened = AsyncMock()
    monkeypatch.setattr(module, "open_capture_settings", opened)
    async with shared_server(tmp_path, skip_auth=True, share_computer=True) as (_, app, client):
        assert (await client.post("/api/browser/macos/password", json={"password": "unused"})).status_code in {404, 405}
        thread = (await app.create_thread()).thread_id
        assert (await client.post(f"/api/threads/{thread}/browser/clipboard", json={})).status_code in {404, 405}
        assert (await client.post("/api/browser/macos/settings")).status_code == 204
        opened.assert_awaited_once()
