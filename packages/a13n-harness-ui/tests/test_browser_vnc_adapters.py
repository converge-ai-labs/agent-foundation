"""Opt-in real Linux coverage for alternative managed VNC servers."""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import time
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.browser import BrowserAccessRequest, HostBrowsers
from a13n_harness_ui.browser_setup import scan_browsers
from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.errors import HarnessUiError

from .test_host_browser_linux import key, rfb, site  # noqa: F401

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "linux" or os.environ.get("A13N_BROWSER_REAL_TEST") != "1",
        reason="Optional Linux Host VNC validation",
    ),
]


@pytest.fixture(params=["x11vnc", "wayvnc"])
async def alternative(request):
    scan = await scan_browsers()
    candidate = next((vnc for vnc in scan.vncs if vnc.kind == request.param), None)
    if not candidate or not scan.browsers:
        pytest.skip(f"Compatible {request.param} and Chromium are required")
    if request.param == "wayvnc" and os.geteuid() == 0:
        pytest.skip("Sway requires a non-root Host process")
    return BrowserConfiguration(executable=scan.browsers[0].path, vnc_executable=candidate.path)


async def test_native_readonly_control_and_shared_profile(tmp_path, site, alternative):  # noqa: F811
    from playwright.async_api import expect

    browsers = HostBrowsers(root=tmp_path / "browser", available=True, authorize=AsyncMock(), configuration=alternative)
    state = await browsers.enable("thread-adapter")
    try:
        backend = browsers.backend("thread-adapter", state.session_id)
        await backend.act("navigate", value=site)
        session = browsers.session("thread-adapter", state.session_id)
        assert session.page and session.context
        assert session.desktop and session.desktop.kind != "tigervnc"
        assert not session.state.resize_available
        await session.page.bring_to_front()
        await session.page.locator("input").focus()
        await session.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await session.page.evaluate("navigator.clipboard.writeText('controller clipboard')")
        _, frame_reader, readonly = await rfb(browsers, "thread-adapter", state.session_id)
        readonly.write(b"\x03\x00\x00\x00\x00\x00\x00\x20\x00\x20")
        await readonly.drain()
        frame = await asyncio.wait_for(frame_reader.readexactly(4), 10)
        assert frame[0] == 0 and int.from_bytes(frame[2:], "big") > 0
        rectangle = await asyncio.wait_for(frame_reader.readexactly(12), 10)
        assert int.from_bytes(rectangle[4:6], "big") > 0
        key(readonly, ord("x"))
        text = b"read-only clipboard injection"
        readonly.write(struct.pack(">B3xI", 6, len(text)) + text)
        await readonly.drain()
        # A following browser round trip lets native input settle; an editable
        # field stays empty even if a viewer bypasses noVNC's viewOnly flag.
        await asyncio.sleep(0.15)
        await expect(session.page.locator("input")).to_have_value("")
        assert await session.page.evaluate("navigator.clipboard.readText()") == "controller clipboard"
        old_password = session.password
        await browsers.disconnect(session, readonly)
        other = await browsers.enable("thread-other")
        await browsers.backend("thread-other", other.session_id).act("navigate", value=site)
        other_page = session.windows["thread-other"].page
        first_page = session.windows["thread-adapter"].page
        assert other_page and first_page and other_page is not first_page
        control = await browsers.control("thread-adapter", state.session_id, human=True)
        if old_password:
            with pytest.raises(PermissionError):
                await rfb(browsers, "thread-adapter", state.session_id, native_password=old_password)
        _, _, writable = await rfb(browsers, "thread-adapter", state.session_id, control.control_token)
        # Wait for the native pointer event, not CDP's synthetic focus state,
        # before setting the input caret on a newly created Wayland keyboard.
        await session.page.evaluate(
            "window.vncPointer = false; window.vncMoved = false; addEventListener('pointerdown', () => window.vncPointer = true, {once:true}); addEventListener('pointermove', () => window.vncMoved = true, {once:true})"
        )
        writable.write(struct.pack(">BBHH", 5, 0, 700, 550))
        await writable.drain()
        await session.page.wait_for_function("window.vncMoved === true")
        writable.write(struct.pack(">BBHH", 5, 1, 700, 550) + struct.pack(">BBHH", 5, 0, 700, 550))
        await writable.drain()
        await session.page.wait_for_function("window.vncPointer === true")
        await session.page.locator("input").focus()
        key(writable, ord("y"))
        await writable.drain()
        await expect(session.page.locator("input")).to_have_value("y")
        await session.context.add_cookies(
            [{"name": "adapter", "value": "retained", "url": site, "expires": time.time() + 3600}]
        )
        writable.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE1))  # Hold Shift across handback.
        await writable.drain()
        await asyncio.sleep(0.1)
        await browsers.control("thread-adapter", state.session_id, human=False)
        control = await browsers.control("thread-adapter", state.session_id, human=True)
        _, _, next_controller = await rfb(browsers, "thread-adapter", state.session_id, control.control_token)
        await session.page.evaluate(
            "window.vncPointer = false; window.vncMoved = false; addEventListener('pointerdown', () => window.vncPointer = true, {once:true}); addEventListener('pointermove', () => window.vncMoved = true, {once:true})"
        )
        next_controller.write(struct.pack(">BBHH", 5, 0, 710, 560))
        await next_controller.drain()
        await session.page.wait_for_function("window.vncMoved === true")
        next_controller.write(struct.pack(">BBHH", 5, 1, 710, 560) + struct.pack(">BBHH", 5, 0, 710, 560))
        await next_controller.drain()
        await session.page.wait_for_function("window.vncPointer === true")
        await session.page.locator("input").focus()
        key(next_controller, ord("z"))
        await next_controller.drain()
        await expect(session.page.locator("input")).to_have_value("yz")
        old_profile = session.profile
        new = await browsers.reset("thread-adapter", state.session_id)
        replacement = browsers.session("thread-adapter", new.session_id)
        assert replacement.profile == old_profile
        assert replacement.context and replacement.state.control == "agent"
        assert not other_page.is_closed()
        assert browsers.state("thread-other").session_id == other.session_id
        assert any(cookie["name"] == "adapter" for cookie in await replacement.context.cookies())
        with pytest.raises(HarnessUiError):
            await browsers.access("thread-adapter", BrowserAccessRequest(session_id=state.session_id))
    finally:
        await browsers.close()
