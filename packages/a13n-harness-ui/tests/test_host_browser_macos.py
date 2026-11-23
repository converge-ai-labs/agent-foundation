"""Opt-in native capture test: set A13N_TEST_MACOS_BROWSER to Chrome.

Requires macOS 14+, a logged-in graphical session and Screen Recording permission.
Only captures temporary Harness-owned windows, in memory; never sends native input.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.browser import BrowserAccessRequest, HostBrowsers
from a13n_harness_ui.browser_capture import WindowPreview
from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.errors import HarnessUiError
from PIL import Image

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "darwin" or not os.environ.get("A13N_TEST_MACOS_BROWSER"),
        reason="Requires explicitly prepared macOS window capture",
    ),
]


@pytest.fixture(autouse=True)
def isolated_user_home():
    # ScreenCaptureKit needs the real logged-in GUI session's HOME. Chromium
    # still receives an explicit temporary profile; no Harness config is loaded.
    pass


async def test_reconnect_preserves_process_tabs_and_never_replays_pending_command(tmp_path, monkeypatch):
    from a13n_harness.capabilities.browser import BrowserError
    from a13n_harness_ui.browser_macos import MacDesktop

    monkeypatch.setattr(MacDesktop, "chromium_arguments", ("--headless=new", "--use-mock-keychain"))
    host = HostBrowsers(
        root=tmp_path,
        available=True,
        authorize=AsyncMock(),
        configuration=BrowserConfiguration(executable=os.environ["A13N_TEST_MACOS_BROWSER"]),
    )
    try:
        await host.prepare()
        assert host.runtime_ready, host.startup_error
        runtime, process = host.runtime, host.process
        bindings = {}
        for thread in ("first", "second"):
            identity = await host.admit(thread)
            item = host.bindings[thread]
            page = await runtime.create(new_window=True)
            await item.scope.attach(page.target)
            bindings[thread] = (identity, page.target)
        backend = host.backend("second", bindings["second"][0])
        await backend.observe()
        pending = asyncio.create_task(backend.js("window.count = (window.count || 0) + 1; new Promise(() => {})"))
        async with asyncio.timeout(10):
            while not await runtime.evaluate(page, "window.count"):
                await asyncio.sleep(0.01)
        await runtime.client.ws.close()
        with pytest.raises(BrowserError) as error:
            await pending
        assert error.value.dispatch == "unknown"
        async with asyncio.timeout(10):
            while host.runtime is runtime or not host.runtime_ready:
                assert not host.runtime_failed, host.startup_error
                await asyncio.sleep(0.01)
        assert host.process is process and process.returncode is None
        for thread, (identity, target) in bindings.items():
            item = host.bindings[thread]
            assert item.identity == identity and item.scope.targets == {target}
            assert item.page.target == target and item.page.requires_observation
        await backend.observe()
        assert await backend.js("window.count") == 1
    finally:
        await host.close()


async def test_native_window_capture_static_live_pause_and_close(tmp_path):
    host = HostBrowsers(
        root=tmp_path / "profile",
        available=True,
        authorize=AsyncMock(),
        configuration=BrowserConfiguration(executable=os.environ["A13N_TEST_MACOS_BROWSER"]),
    )
    try:
        state = await host.enable("native-test")
        state = await host.start("native-test", state.session_id)
        item = host.bindings["native-test"]
        runtime, page = host.runtime, item.page
        assert runtime and page and item.preview_window

        async def connect():
            access = await host.access("native-test", BrowserAccessRequest(session_id=state.session_id))
            assert access.protocol == "jpeg" and not access.control and not access.password
            connection = await host.connect("native-test", access.ticket)
            assert isinstance(connection, WindowPreview)
            return connection

        preview = await connect()
        first = await preview.receive()
        image = Image.open(io.BytesIO(first))
        assert image.format == "JPEG" and min(image.size) > 100
        # Real stream updates after a static initial frame, without page animation.
        assert await runtime.evaluate(page, "document.body.style.background='red'") == "red"
        async with asyncio.timeout(10):
            while True:
                changed = Image.open(io.BytesIO(await preview.receive())).convert("RGB")
                red, green, blue = changed.getpixel((changed.width // 2, changed.height // 2))
                if red > 200 and green < 50 and blue < 50:
                    break
        # Another Thread gets a distinct exact native window; never switch the first preview.
        other = await host.enable("other")
        await host.start("other", other.session_id)
        assert host.bindings["other"].preview_window != item.preview_window
        control = await host.control("native-test", state.session_id, human=True)
        assert control.state.control == "human" and preview.is_closing()
        with pytest.raises(HarnessUiError, match="user controls"):
            await host.backend("native-test", state.session_id).tabs("list")
        paused = await connect()
        assert (await paused.receive()).startswith(b"\xff\xd8")
        await host.control("native-test", state.session_id, human=False)
        assert paused.is_closing() and page.requires_observation
        assert await host.backend("native-test", state.session_id).tabs("list")
        final = await connect()
        await host.backend("native-test", state.session_id).tabs("close", target=page.target)
        async with asyncio.timeout(5):
            with pytest.raises(HarnessUiError):
                while True:
                    await final.receive()
        assert host.state("native-test").status == "ready"
        assert host.state("other").status == "ready"
    finally:
        await host.close()
