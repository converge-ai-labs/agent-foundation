"""Opt-in validation of the real Linux display, RFB permissions and noVNC UI.

Set A13N_BROWSER_REAL_TEST=1 and PLAYWRIGHT_BROWSERS_PATH to the installed
Chromium cache explicitly: repository fixtures isolate HOME during tests.
"""

from __future__ import annotations

import asyncio
import os
import random
import signal
import socket
import stat
import struct
import sys
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import uvicorn
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.browser import (
    VIEWPORTS,
    BrowserAccessRequest,
    BrowserSession,
    BrowserViewportRequest,
    HostBrowsers,
)
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "linux" or os.environ.get("A13N_BROWSER_REAL_TEST") != "1",
        reason="Optional Linux Host VNC validation",
    ),
]


@pytest.fixture
async def site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"""<title>Host browser login</title><h1>Welcome to the Host browser</h1><p>Log in here, then hand control back to the Agent.</p><label>Password <input id="password" type="password"></label><button onclick="document.querySelector('output').textContent='Signed in';document.cookie='login=retained;max-age=3600;path=/';document.querySelector('input').value=''">Sign in</button><output>Waiting for login</output>"""
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        worker.join()


async def rfb(
    browsers: HostBrowsers, thread_id: str, session_id: str, token: str = "", *, native_password: str | None = None
):
    access = await browsers.access(thread_id, BrowserAccessRequest(session_id=session_id, control_token=token))
    session, reader, writer = await browsers.connect(thread_id, access.ticket)
    assert (await asyncio.wait_for(reader.readexactly(12), 10)).startswith(b"RFB ")
    writer.write(b"RFB 003.008\n")
    count = (await reader.readexactly(1))[0]
    security = await reader.readexactly(count)
    assert 2 in security
    writer.write(b"\x02")
    challenge = await reader.readexactly(16)
    # Independent test client for TigerVNC's standard VncAuth handshake.
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    password = bytes(int(f"{byte:08b}"[::-1], 2) for byte in (native_password or access.password).encode())
    encryptor = Cipher(TripleDES(password * 3), modes.ECB()).encryptor()
    writer.write(encryptor.update(challenge) + encryptor.finalize())
    if await reader.readexactly(4) != b"\x00\x00\x00\x00":
        await browsers.disconnect(session, writer)
        raise PermissionError("Native VNC credentials were revoked.")
    writer.write(b"\x01")
    header = await reader.readexactly(24)
    await reader.readexactly(struct.unpack(">I", header[-4:])[0])
    return session, reader, writer


def key(writer, keysym):
    writer.write(struct.pack(">BBHI", 4, 1, 0, keysym))
    writer.write(struct.pack(">BBHI", 4, 0, 0, keysym))


async def test_server_rejects_readonly_rfb_input_and_handoff_preserves_profile(tmp_path, site):
    from playwright.async_api import expect

    async def authorize(thread_id):
        pass

    browsers = HostBrowsers(root=tmp_path / "profiles", available=True, authorize=authorize)
    state = await browsers.enable("thread-test")
    backend = browsers.backend("thread-test", state.session_id)
    try:
        await backend.act("navigate", value=site)
        session = browsers.session("thread-test", state.session_id)
        assert session.page is not None
        assert stat.S_ISSOCK(os.stat(session.control_socket).st_mode)
        assert stat.S_IMODE(os.stat(session.control_socket).st_mode) == 0o600
        await session.page.bring_to_front()
        await session.page.locator("input").focus()
        assert session.context is not None
        await session.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await session.page.evaluate("navigator.clipboard.writeText('controller-only clipboard')")
        _, readonly_reader, readonly = await rfb(browsers, "thread-test", state.session_id)
        key(readonly, ord("x"))
        text = b"readonly-must-not-set"
        readonly.write(struct.pack(">B3xI", 6, len(text)) + text)
        await readonly.drain()
        # Bounded negative observation: the read-only server sends neither clipboard data nor input.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(readonly_reader.read(1), 0.25)
        await expect(session.page.locator("input")).to_have_value("")
        assert await session.page.evaluate("navigator.clipboard.readText()") == "controller-only clipboard"
        await browsers.disconnect(session, readonly)
        control = await browsers.control("thread-test", state.session_id, human=True)
        old_password = session.password
        _, _, controller = await rfb(browsers, "thread-test", state.session_id, control.control_token)
        key(controller, ord("x"))
        await controller.drain()
        await expect(session.page.locator("input")).to_have_value("x")
        with pytest.raises(HarnessUiError, match="user controls"):
            await backend.observe()
        controller.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE3))  # Leave Ctrl held across handback.
        await controller.drain()
        await browsers.control("thread-test", state.session_id, human=False)
        with pytest.raises(PermissionError, match="revoked"):
            await rfb(browsers, "thread-test", state.session_id, native_password=old_password)
        control = await browsers.control("thread-test", state.session_id, human=True)
        _, _, controller = await rfb(browsers, "thread-test", state.session_id, control.control_token)
        key(controller, ord("a"))
        await controller.drain()
        await expect(session.page.locator("input")).to_have_value("xa")
        await session.page.get_by_role("button", name="Sign in").click()
        await browsers.control("thread-test", state.session_id, human=False)
        assert controller.is_closing()
        with pytest.raises(HarnessUiError, match="after handback"):
            await backend.act("press", value="Enter")
        assert "Signed in" in (await backend.observe()).text
        await browsers.stop("thread-test", state.session_id)
        new = await browsers.start("thread-test", state.session_id, explicit=True)
        replacement = browsers.backend("thread-test", new.session_id)
        await replacement.observe()
        await replacement.act("navigate", value=site)
        page = browsers.session("thread-test", new.session_id).page
        assert page is not None
        assert "login=retained" in await page.evaluate("document.cookie")
    finally:
        await browsers.close()


async def test_agent_cursor_tracks_actions_without_intercepting_input_or_surviving_takeover(tmp_path, site):
    from playwright.async_api import expect

    async def authorize(thread_id):
        pass

    browsers = HostBrowsers(root=tmp_path / "profiles", available=True, authorize=authorize)
    state = await browsers.enable("thread-test")
    backend = browsers.backend("thread-test", state.session_id)
    try:
        await backend.act("navigate", value=site)
        session = browsers.session("thread-test", state.session_id)
        page = session.page
        assert page is not None
        await page.set_content(
            """<style>body { padding:40px } input,button { display:block; margin:24px; padding:16px }</style>
            <label>Name<input placeholder="Your name"></label>
            <button onclick="document.querySelector('output').textContent='Done'">Say hello</button><output></output>"""
        )
        observation = await backend.observe()
        field = next(element.target for element in observation.elements if "Your name" in element.description)
        await backend.act("fill", target=field, revision=observation.revision, value="Visitor")
        cursor = page.locator(f"#{session.cursor.id}")
        await expect(cursor).to_be_visible()
        await expect(cursor.locator("img")).to_have_js_property("naturalWidth", 192)
        await expect(page.locator("input")).to_have_value("Visitor")
        assert await cursor.get_attribute("aria-hidden") == "true"
        assert await cursor.evaluate("e => e.matches(':popover-open')")
        before = await cursor.bounding_box()
        assert before is not None
        assert "Agent" not in (await backend.observe()).text

        observation = await backend.observe()
        button = next(element.target for element in observation.elements if "Say hello" in element.description)
        await backend.act("click", target=button, revision=observation.revision)
        await expect(page.locator("output")).to_have_text("Done")
        after = await cursor.bounding_box()
        bounds = await page.get_by_role("button", name="Say hello").bounding_box()
        assert after is not None and bounds is not None
        assert bounds["x"] <= after["x"] <= bounds["x"] + bounds["width"]
        assert bounds["y"] <= after["y"] <= bounds["y"] + bounds["height"]
        assert after["y"] != before["y"]
        assert await cursor.locator(".pulse").evaluate("e => e.classList.contains('active')")
        assert (
            await page.evaluate("([x,y]) => document.elementFromPoint(x,y)?.tagName", [after["x"], after["y"]])
            == "BUTTON"
        )

        await page.emulate_media(reduced_motion="no-preference")
        movement = await cursor.evaluate("""async host => {
            const pointer = host.shadowRoot.querySelector('.pointer');
            await Promise.all(pointer.getAnimations().map(animation => animation.finished));
            document.dispatchEvent(new MouseEvent('mousemove', {clientX:700, clientY:350}));
            const start = pointer.getBoundingClientRect();
            const animations = pointer.getAnimations();
            await Promise.all(animations.map(animation => animation.finished));
            const end = pointer.getBoundingClientRect();
            return {animated:animations.length > 0, start:[start.x,start.y], end:[end.x,end.y]};
        }""")
        assert movement["animated"]
        assert movement["start"] != [700, 350]
        assert movement["end"] == [700, 350]
        await page.emulate_media(reduced_motion="reduce")
        reduced = await cursor.evaluate("""host => {
            document.dispatchEvent(new MouseEvent('mousemove', {clientX:450, clientY:300}));
            const pointer = host.shadowRoot.querySelector('.pointer');
            const rect = pointer.getBoundingClientRect();
            return {animated:pointer.getAnimations().length > 0, position:[rect.x,rect.y]};
        }""")
        assert not reduced["animated"]
        assert reduced["position"] == [450, 300]

        await browsers.control("thread-test", state.session_id, human=True)
        await expect(cursor).to_have_count(0)
        await page.get_by_role("button", name="Say hello").click()
        await expect(cursor).to_have_count(0)
        await browsers.control("thread-test", state.session_id, human=False)
        await backend.observe()
        await backend.act("press", value="Tab")
        await expect(cursor).to_be_visible()
        await backend.act("navigate", value=site)
        await expect(cursor).to_have_count(0)
    finally:
        await browsers.close()


@pytest.mark.parametrize("platform", ["Linux x86_64", "MacIntel", "Win32"])
async def test_real_novnc_panel_login_handback_and_viewer_closure(tmp_path, site, platform):
    from a13n_harness.capabilities.browser import BrowserUseCapability
    from playwright.async_api import async_playwright, expect

    root = _write_configuration(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    try:
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=root, host_mode="webui", share_computer=True
        ) as app:
            thread = await app.create_thread()
            state = await app.enable_browser(thread.thread_id)
            backend = app.browsers().backend(thread.thread_id, state.session_id)
            await backend.act("navigate", value=site)
            session = app.browsers().session(thread.thread_id, state.session_id)

            @asynccontextmanager
            async def borrowed():
                yield app

            server = uvicorn.Server(
                uvicorn.Config(create_webui(borrowed, api_key="vnc-test"), log_level="warning", access_log=False)
            )
            running = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with async_playwright() as driver:
                    viewer = await driver.chromium.launch(channel="chromium", headless=True)
                    try:
                        page = await viewer.new_page(viewport={"width": 1440, "height": 1000})
                        await page.add_init_script(
                            f"Object.defineProperty(navigator, 'platform', {{get: () => {platform!r}}})"
                        )
                        modifier = "Meta" if platform == "MacIntel" else "Control"
                        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
                        await page.goto(f"http://127.0.0.1:{port}/threads/{thread.thread_id}#api_key=vnc-test")
                        await page.get_by_role("button", name="Turn off reminders", exact=True).click()
                        await page.get_by_role("button", name="Browser", exact=True).click()
                        await expect(page.get_by_label("Remote browser desktop")).to_have_attribute(
                            "aria-busy", "false", timeout=20000
                        )
                        await page.get_by_role("button", name="Expand drawer", exact=True).click()
                        await page.get_by_role("button", name="Read-only browser", exact=True).click()
                        await expect(
                            page.get_by_text(
                                "Read only. Select Take control to interact with this browser.", exact=True
                            )
                        ).to_be_visible()
                        await page.wait_for_function("""() => {
                            const canvas = document.querySelector('[data-browser-screen] canvas');
                            if (!canvas || canvas.width < 100) return false;
                            const pixel = canvas.getContext('2d').getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data;
                            return pixel[0] + pixel[1] + pixel[2] > 100;
                        }""")
                        await page.evaluate("""() => {
                            window.retainedFrames = 0;
                            const saved = document.querySelector('[data-browser-frame="retained"]');
                            new MutationObserver(() => {
                                if (!saved.hidden && saved.width > 100 && saved.height > 100)
                                    window.retainedFrames++;
                            }).observe(saved, { attributes: true, attributeFilter: ['hidden'] });
                        }""")
                        await page.get_by_role("button", name="Take control", exact=True).click()
                        await expect(page.get_by_label("Remote browser desktop")).to_have_attribute(
                            "aria-busy", "false", timeout=20000
                        )
                        assert await page.evaluate("window.retainedFrames") > 0
                        await expect(page.locator('[data-browser-frame="retained"]')).to_be_hidden()
                        await expect(page.get_by_role("button", name="Read-only browser", exact=True)).to_have_count(0)
                        await page.get_by_role("combobox", name="Browser viewport size").select_option("phone")
                        await page.wait_for_function("""() => {
                            const canvas = document.querySelector('[data-browser-screen] canvas');
                            return canvas && canvas.height > canvas.width;
                        }""")
                        await expect(page.get_by_label("Remote browser desktop")).to_have_attribute(
                            "aria-busy", "false"
                        )
                        assert session.page is not None
                        assert await session.page.evaluate("[innerWidth, innerHeight]") == [390, 844]
                        assert session.page is not None
                        # Focus only; all entered text crosses the actual noVNC/RFB input channel.
                        await session.page.bring_to_front()
                        await session.page.locator("input").focus()
                        await page.locator("[data-browser-screen] canvas").focus()
                        await page.keyboard.type("replace me")
                        await expect(session.page.locator("input")).to_have_value("replace me")
                        await page.keyboard.press(f"{modifier}+a")
                        await page.evaluate("navigator.clipboard.writeText('human-secret中文🙂')")
                        await page.locator("[data-browser-screen] canvas").focus()
                        await page.keyboard.press(f"{modifier}+v")
                        await expect(session.page.locator("input")).to_have_value("human-secret中文🙂")
                        await expect(page.get_by_text("Text and clipboard", exact=True)).to_have_count(0)
                        with pytest.raises(HarnessUiError, match="user controls"):
                            await backend.screenshot()
                        native = BrowserUseCapability(backend).get_toolset().tools
                        pending = asyncio.create_task(native["browser_observe"].function_schema.call({}, None))
                        await asyncio.sleep(0)
                        assert not pending.done()
                        # Tab/Enter go through the desktop viewer to activate the remote login button.
                        await page.locator("[data-browser-screen] canvas").focus()
                        await page.keyboard.press("Tab")
                        await page.keyboard.press("Enter")
                        await expect(session.page.locator("output")).to_have_text("Signed in")
                        # Native remote copying propagates back to the viewer's clipboard without a button.
                        await session.page.evaluate("""() => {
                            const field = document.createElement('textarea');
                            field.id='clipboard-fixture'; field.value='远端 clipboard ✨';
                            document.body.append(field); field.focus();
                        }""")
                        # A held modifier supports successive shortcuts without becoming Alt or getting stuck.
                        await page.keyboard.down(modifier)
                        await page.keyboard.press("a")
                        await page.keyboard.press("c")
                        await page.keyboard.up(modifier)
                        await page.wait_for_function(
                            "() => navigator.clipboard.readText().then(text => text === '远端 clipboard ✨')"
                        )
                        await page.keyboard.press(f"{modifier}+x")
                        await expect(session.page.locator("#clipboard-fixture")).to_have_value("")
                        await page.keyboard.press(f"{modifier}+v")
                        await expect(session.page.locator("#clipboard-fixture")).to_have_value("远端 clipboard ✨")
                        await page.keyboard.press(f"{modifier}+a")
                        await page.keyboard.type("replacement")
                        await expect(session.page.locator("#clipboard-fixture")).to_have_value("replacement")
                        # Undo isn't intercepted by our clipboard bridge; noVNC forwards it with the mapped modifier.
                        await page.keyboard.press(f"{modifier}+z")
                        await expect(session.page.locator("#clipboard-fixture")).to_have_value("远端 clipboard ✨")
                        # Keyup follows local focus. Moving to the toolbar while
                        # Ctrl is held must not leave Ctrl pressed on the Host.
                        for control_key in ("ControlLeft", "ControlRight"):
                            await page.keyboard.press(f"{modifier}+a")
                            await page.keyboard.down(control_key)
                            await page.get_by_role("combobox", name="Browser viewport size").focus()
                            await page.keyboard.up(control_key)
                            await page.locator("[data-browser-screen] canvas").focus()
                            await page.keyboard.type("a")
                            await expect(session.page.locator("#clipboard-fixture")).to_have_value("a")
                        if platform == "MacIntel":
                            # High-entropy Unicode exceeds a single WebSocket input frame and must
                            # survive noVNC's native extended-clipboard compression and chunking.
                            generator = random.Random(1006)
                            large = "剪贴板🙂\n" + "".join(
                                chr(0x4E00 + generator.randrange(20000)) for _ in range(40000)
                            )
                            await page.evaluate("text => navigator.clipboard.writeText(text)", large)
                            await page.keyboard.press(f"{modifier}+a")
                            await page.keyboard.press(f"{modifier}+v")
                            await expect(session.page.locator("#clipboard-fixture")).to_have_value(large)
                            remote = "远端新文本🙂\n" + large
                            await session.page.locator("#clipboard-fixture").fill(remote)
                            await page.keyboard.press(f"{modifier}+a")
                            await page.keyboard.press(f"{modifier}+c")
                            await page.wait_for_function(
                                "text => navigator.clipboard.readText().then(value => value === text)", arg=remote
                            )
                        await page.get_by_role("button", name="Hand back to Agent", exact=True).click()
                        await expect(page.get_by_label("Remote browser desktop")).to_have_attribute(
                            "aria-busy", "false", timeout=20000
                        )
                        assert "fresh browser_observe" in await pending
                        assert "Signed in" in await native["browser_observe"].function_schema.call({}, None)
                        await page.get_by_role("button", name="Close browser viewer", exact=True).click()
                        assert (await app.browser_state(thread.thread_id)).status == "ready"
                        await session.page.close()
                        await page.get_by_role("button", name="Browser", exact=True).click()
                        await expect(page.get_by_text("Browser stopped", exact=True)).to_be_visible()
                        assert (await app.browser_state(thread.thread_id)).status == "stopped"
                        await page.get_by_role("button", name="Start browser", exact=True).click()
                        await expect(page.get_by_label("Remote browser desktop")).to_have_attribute(
                            "aria-busy", "false", timeout=20000
                        )
                        reopened = await app.browser_state(thread.thread_id)
                        assert reopened.session_id != state.session_id
                        replacement = app.browsers().backend(thread.thread_id, reopened.session_id)
                        await replacement.observe()
                        await replacement.act("navigate", value=site)
                        new_page = app.browsers().session(thread.thread_id, reopened.session_id).page
                        assert new_page is not None
                        assert "login=retained" in await new_page.evaluate("document.cookie")
                    finally:
                        await viewer.close()
            finally:
                server.should_exit = True
                await running
    finally:
        listener.close()


@pytest.mark.parametrize("exit_kind", ["last-page", "browser", "browser-crash", "display", "window-manager"])
async def test_real_browser_exit_cleans_runtime_and_reopens_profile(tmp_path, site, monkeypatch, exit_kind):
    processes = {}
    watch = BrowserSession.watch_process

    def watch_process(self, process, name):
        processes[name] = process
        watch(self, process, name)

    monkeypatch.setattr(BrowserSession, "watch_process", watch_process)

    async def authorize(thread_id):
        pass

    browsers = HostBrowsers(root=tmp_path / "profiles", available=True, authorize=authorize)
    state = await browsers.enable("thread-exit")
    backend = browsers.backend("thread-exit", state.session_id)
    try:
        await backend.act("navigate", value=site)
        session = browsers.session("thread-exit", state.session_id)
        assert session.page is not None
        assert session.context is not None
        await session.page.get_by_role("button", name="Sign in").click()
        if exit_kind in {"display", "browser-crash"}:
            # Abrupt exit need not flush new cookies. Seed an actually persisted
            # login through a clean shutdown before testing crash recovery.
            await browsers.stop("thread-exit", state.session_id)
            state = await browsers.start("thread-exit", state.session_id, explicit=True)
            backend = browsers.backend("thread-exit", state.session_id)
            await backend.observe()
            await backend.act("navigate", value=site)
            session = browsers.session("thread-exit", state.session_id)
        assert session.page is not None
        assert session.context is not None
        browser_pid = None
        if exit_kind == "browser-crash":
            assert session.context.browser is not None
            cdp = await session.context.browser.new_browser_cdp_session()
            info = await cdp.send("SystemInfo.getProcessInfo")
            browser_pid = next(process["id"] for process in info["processInfo"] if process["type"] == "browser")
        await session.page.bring_to_front()
        await browsers.control("thread-exit", state.session_id, human=True)
        pending = asyncio.create_task(backend.wait_for_handback())
        await asyncio.sleep(0)
        assert not pending.done()
        if exit_kind == "last-page":
            await session.page.close()
        elif exit_kind == "browser":
            # Close the actual Chromium window through VNC/Openbox, as a user
            # would. Browser.close() is a force-quit automation primitive.
            _, _, writer = await rfb(browsers, "thread-exit", state.session_id, session.control_token)
            writer.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE9))  # Alt down
            key(writer, 0xFFC1)  # F4
            writer.write(struct.pack(">BBHI", 4, 0, 0, 0xFFE9))
            await writer.drain()
        elif exit_kind == "browser-crash":
            assert browser_pid is not None
            os.kill(browser_pid, signal.SIGKILL)
        else:
            name = {
                "display": "The virtual display",
                "window-manager": "The window manager",
            }[exit_kind]
            processes[name].kill()
        # No state polling or open viewer is needed to detect the exit.
        with pytest.raises(HarnessUiError, match="session changed"):
            await asyncio.wait_for(pending, 15)
        assert session.state.status == "stopped"
        assert not session.control_token
        old_processes = tuple(processes.values())
        await asyncio.wait_for(session.close(), 20)
        assert all(process.returncode is not None for process in old_processes)
        assert session.context is session.page is None
        with pytest.raises(HarnessUiError, match="session changed"):
            await backend.act("navigate", value=site)
        reopened = await browsers.start("thread-exit", state.session_id, explicit=True)
        assert reopened.status == "ready"
        assert reopened.session_id != state.session_id
        replacement = browsers.backend("thread-exit", reopened.session_id)
        with pytest.raises(HarnessUiError, match="after handback"):
            await replacement.act("press", value="Enter")
        await replacement.observe()
        await replacement.act("navigate", value=site)
        page = browsers.session("thread-exit", reopened.session_id).page
        assert page is not None
        assert "login=retained" in await page.evaluate("document.cookie")
    finally:
        await browsers.close()


async def test_reset_restores_an_offscreen_window_and_keeps_the_profile(tmp_path, site):
    async def authorize(thread_id):
        pass

    browsers = HostBrowsers(root=tmp_path / "profiles", available=True, authorize=authorize)
    state = await browsers.enable("thread-reset")
    backend = browsers.backend("thread-reset", state.session_id)
    try:
        await backend.act("navigate", value=site)
        session = browsers.session("thread-reset", state.session_id)
        assert session.context is not None
        assert session.page is not None
        await session.page.get_by_role("button", name="Sign in").click()
        cdp = await session.context.new_cdp_session(session.page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
        await cdp.send(
            "Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"left": -3000, "top": -2000}}
        )
        before = await cdp.send("Browser.getWindowBounds", {"windowId": window["windowId"]})
        assert before["bounds"]["left"] < 0
        reset = await browsers.reset("thread-reset", state.session_id)
        assert reset.status == "ready"
        assert reset.control == "agent"
        assert reset.session_id != state.session_id
        new = browsers.session("thread-reset", reset.session_id)
        assert new.context is not None
        assert new.page is not None
        cdp = await new.context.new_cdp_session(new.page)
        window = await cdp.send("Browser.getWindowForTarget")
        bounds = window["bounds"]
        assert 0 <= bounds["left"] < 1280
        assert 0 <= bounds["top"] < 800
        assert bounds["windowState"] != "minimized"
        replacement = browsers.backend("thread-reset", reset.session_id)
        await replacement.observe()
        await replacement.act("navigate", value=site)
        assert "login=retained" in await new.page.evaluate("document.cookie")
        with pytest.raises(HarnessUiError, match="session changed"):
            await backend.observe()
    finally:
        await browsers.close()


async def test_viewport_presets_reflow_live_pages_and_resize_the_entire_vnc_desktop(tmp_path, site):
    async def authorize(thread_id):
        pass

    browsers = HostBrowsers(root=tmp_path / "profiles", available=True, authorize=authorize)
    state = await browsers.enable("thread-size")
    backend = browsers.backend("thread-size", state.session_id)
    try:
        await backend.act("navigate", value=site)
        session = browsers.session("thread-size", state.session_id)
        assert session.page is not None
        assert session.context is not None
        await session.page.evaluate("window.keptAcrossResize = 'retained'")
        control = await browsers.control("thread-size", state.session_id, human=True)
        _, _, writer = await rfb(browsers, "thread-size", state.session_id, control.control_token)
        for viewport in VIEWPORTS:
            result = await browsers.resize(
                "thread-size",
                BrowserViewportRequest(
                    session_id=state.session_id, control_token=control.control_token, preset=viewport.preset
                ),
            )
            assert result.session_id == state.session_id
            assert result.control == "human"
            assert result.viewport == viewport.preset
            assert not writer.is_closing()
            assert await session.page.evaluate("[innerWidth, innerHeight]") == [viewport.width, viewport.height]
            assert await session.page.evaluate("matchMedia('(max-width: 600px)').matches") == (viewport.width <= 600)
            assert await session.page.evaluate("window.keptAcrossResize") == "retained"
            cdp = await session.context.new_cdp_session(session.page)
            bounds = (await cdp.send("Browser.getWindowForTarget"))["bounds"]
            await cdp.detach()
            display = await session.randr("--query")
            assert f"current {bounds['width']} x {bounds['height']}" in display
        page = await session.context.new_page()
        async with asyncio.timeout(10):
            while session.page_tasks:
                await asyncio.gather(*tuple(session.page_tasks))
        assert await page.evaluate("[innerWidth, innerHeight]") == [844, 390]
        assert browsers.state("thread-size").status == "ready"
        reset = await browsers.reset("thread-size", state.session_id)
        assert reset.viewport == "phone-landscape"
        new = browsers.session("thread-size", reset.session_id)
        assert new.page is not None
        assert await new.page.evaluate("[innerWidth, innerHeight]") == [844, 390]
    finally:
        await browsers.close()
