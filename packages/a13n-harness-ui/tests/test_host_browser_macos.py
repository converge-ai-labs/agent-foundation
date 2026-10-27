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
