"""Opt-in native test: set A13N_TEST_MACOS_BROWSER to the Chrome executable.

Requires an unlocked desktop, enabled Screen Sharing and the Host VNC password.
The test opens browser windows and exercises the system text clipboard.
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.browser import BrowserAccessRequest, BrowserClipboardRequest, HostBrowsers
from a13n_harness_ui.browser_macos import MacDesktop
from a13n_harness_ui.browser_vnc import VERSION
from a13n_harness_ui.configuration.models import BrowserConfiguration

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "darwin" or not os.environ.get("A13N_TEST_MACOS_BROWSER"),
        reason="Requires explicitly prepared macOS Screen Sharing desktop",
    ),
]


async def test_native_screen_sharing_command_clipboard_and_handback(tmp_path):
    host = HostBrowsers(
        root=tmp_path / "profile",
        available=True,
        authorize=AsyncMock(),
        configuration=BrowserConfiguration(executable=os.environ["A13N_TEST_MACOS_BROWSER"]),
    )
    try:
        state = await host.enable("native-test")
        await host.backend("native-test", state.session_id).navigate("about:blank")
        runtime, page = host.runtime, host.bindings["native-test"].page
        assert runtime and page and isinstance(host.desktop, MacDesktop)
        assert not host.state("native-test").resize_available
        await runtime.evaluate(
            page,
            "document.body.innerHTML='<textarea autofocus></textarea>';document.querySelector('textarea').focus();window.metaSeen=false;document.addEventListener('keydown', e => {if(e.metaKey)window.metaSeen=true})",
        )
        control = await host.control("native-test", state.session_id, human=True)
        access = await host.access(
            "native-test", BrowserAccessRequest(session_id=state.session_id, control_token=control.control_token)
        )
        assert access.password == ""
        connection = await host.connect("native-test", access.ticket)
        assert await connection.read(65536) == VERSION
        await connection.send(VERSION + b"\x01\x01")
        assert await connection.read(65536) == b"\x01\x01"
        assert await connection.read(65536) == b"\0\0\0\0"
        init = await connection.read(65536)
        assert min(struct.unpack(">HH", init[:4])) > 100
        await connection.send(struct.pack(">BBHi", 2, 0, 1, 0) + struct.pack(">BBHHHH", 3, 0, 0, 0, 32, 32))
        assert await asyncio.wait_for(connection.read(65536), 10)
        previous = await host.desktop.clipboard(None)
        try:
            text = "Native Unicode paste 中文"
            await host.clipboard(
                "native-test",
                BrowserClipboardRequest(session_id=state.session_id, control_token=control.control_token, text=text),
            )
            await connection.send(
                b"".join(
                    struct.pack(">BBHI", 4, down, 0, key)
                    for key, down in [(0xFFE9, 1), (ord("v"), 1), (ord("v"), 0), (0xFFE9, 0)]
                )
            )
            async with asyncio.timeout(5):
                while await runtime.evaluate(page, "document.querySelector('textarea').value") != text:
                    await asyncio.sleep(0.025)
            assert await runtime.evaluate(page, "window.metaSeen")
            await host.control("native-test", state.session_id, human=False)
            assert connection.is_closing() and not host.connections
            assert await host.backend("native-test", state.session_id).tabs("list")
        finally:
            await host.desktop.clipboard(previous)
    finally:
        await host.close()
