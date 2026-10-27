"""Opt-in headed Chromium/cdp-use integration against native VNC adapters."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock

import pytest
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.actions import Click, Fill, Ref, TextCondition
from a13n_harness_ui.browser import BrowserAccessRequest, BrowserViewportRequest, HostBrowsers
from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.errors import HarnessUiError

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Opt-in headed Linux VNC validation",
    ),
]


@pytest.fixture
async def site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"""<!doctype html><title>Native VNC fixture</title>
            <style>body{font:20px system-ui;padding:30px}input,button{font:inherit;padding:12px}</style>
            <form onsubmit="return false"><input aria-label="Message" name="username" autocomplete="username"><input type="password" aria-label="Password" autocomplete="current-password"><button type="button" onclick="document.cookie='login=retained;max-age=3600;path=/';document.querySelector('output').textContent='Signed in'">Sign in</button>
            <button type="button" onclick="alert('Native VNC alert')">Alert</button><output>Ready</output></form>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join)


@pytest.fixture(params=["tigervnc", "x11vnc", "wayvnc"])
def browser_configuration(request, browser_executable):
    if request.param == "wayvnc" and os.geteuid() == 0:
        pytest.skip("Headless Sway requires a non-root process")
    vnc = shutil.which({"tigervnc": "Xtigervnc", "x11vnc": "x11vnc", "wayvnc": "wayvnc"}[request.param])
    if not vnc:
        pytest.skip("Selected native VNC server is not installed")
    return BrowserConfiguration(executable=browser_executable, vnc_executable=vnc)


async def rfb(browsers, thread, binding, token="", *, native_password=None):
    access = await browsers.access(thread, BrowserAccessRequest(session_id=binding, control_token=token))
    connection = await browsers.connect(thread, access.ticket)
    reader, writer = connection.reader, connection.writer
    assert (await asyncio.wait_for(reader.readexactly(12), 10)).startswith(b"RFB ")
    writer.write(b"RFB 003.008\n")
    count = (await reader.readexactly(1))[0]
    security = await reader.readexactly(count)
    if not access.password:
        assert 1 in security
        writer.write(b"\x01")
        assert await reader.readexactly(4) == b"\0\0\0\0"
    else:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives.ciphers import Cipher, modes

        assert 2 in security
        writer.write(b"\x02")
        challenge = await reader.readexactly(16)
        password = bytes(int(f"{byte:08b}"[::-1], 2) for byte in (native_password or access.password).encode())
        encryptor = Cipher(TripleDES(password * 3), modes.ECB()).encryptor()
        writer.write(encryptor.update(challenge) + encryptor.finalize())
        if await reader.readexactly(4) != b"\0\0\0\0":
            await browsers.disconnect(connection)
            raise PermissionError("Native VNC credentials were revoked")
    writer.write(b"\x01")
    header = await reader.readexactly(24)
    await reader.readexactly(struct.unpack(">I", header[-4:])[0])
    # Finish native client initialization, including WayVNC's virtual input
    # devices, before sending input. noVNC requests its first frame as well.
    writer.write(b"\0\0\0\0" + struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
    writer.write(struct.pack(">BBHi", 2, 0, 1, 0))
    writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 32, 32))
    await writer.drain()
    update = await asyncio.wait_for(reader.readexactly(4), 10)
    assert update[0] == 0
    for _ in range(int.from_bytes(update[2:], "big")):
        _, _, width, height, encoding = struct.unpack(">HHHHi", await reader.readexactly(12))
        assert encoding == 0
        await reader.readexactly(width * height * 4)
    return reader, writer, (int.from_bytes(header[:2], "big"), int.from_bytes(header[2:4], "big"))


def key(writer, keysym):
    writer.write(struct.pack(">BBHI", 4, 1, 0, keysym) + struct.pack(">BBHI", 4, 0, 0, keysym))


async def wait_value(runtime, page, expression, expected):
    async with asyncio.timeout(5):
        while await runtime.evaluate(page, expression) != expected:
            await asyncio.sleep(0.025)


async def wait_input_paint(reader, writer, point):
    """Wait for the fixture input's distinct color, not the white page behind it."""
    async with asyncio.timeout(5):
        while True:
            writer.write(struct.pack(">BBHHHH", 3, 0, round(point["x"]), round(point["y"]), 1, 1))
            await writer.drain()
            update = await reader.readexactly(4)
            assert update[0] == 0
            for _ in range(int.from_bytes(update[2:], "big")):
                _, _, width, height, encoding = struct.unpack(">HHHHi", await reader.readexactly(12))
                assert encoding == 0
                pixels = await reader.readexactly(width * height * 4)
                if pixels[:3] == b"\x5b\xad\x25":
                    return


async def test_native_viewing_control_lifecycle_and_profile(tmp_path, site, browser_configuration, request):
    browsers = HostBrowsers(
        root=tmp_path / "browser",
        available=True,
        authorize=AsyncMock(),
        configuration=browser_configuration,
        no_sandbox=request.config.getoption("--browser-no-sandbox"),
    )
    try:
        state = await browsers.enable("thread-a")
        backend = browsers.backend("thread-a", state.session_id)
        await backend.navigate(site)
        runtime = browsers.runtime
        page = browsers.bindings["thread-a"].page
        assert runtime and page and browsers.desktop
        if browsers.desktop.desktop.window_manager:
            async with asyncio.timeout(5):
                while (await runtime.window(page))["bounds"]["windowState"] != "maximized":
                    await asyncio.sleep(0.025)
        assert stat.S_IMODE(os.stat(browsers.desktop.control_socket).st_mode) == 0o600
        await runtime.evaluate(
            page, "const input=document.querySelector('input');input.style.background='rgb(37,173,91)';input.focus()"
        )
        reader, readonly, dimensions = await rfb(browsers, "thread-a", state.session_id)
        assert min(dimensions) > 100
        readonly.write(b"\x03\0\0\0\0\0\0\x20\0\x20")
        await readonly.drain()
        update = await asyncio.wait_for(reader.readexactly(4), 10)
        assert update[0] == 0 and int.from_bytes(update[2:], "big") > 0
        key(readonly, ord("x"))
        await readonly.drain()
        # Negative native-input assertion: a subsequent round trip alone does
        # not settle an X/Wayland input queue, so allow a bounded quiet interval.
        await asyncio.sleep(0.15)
        assert await runtime.evaluate(page, "document.querySelector('input').value") == ""
        await browsers.disconnect(
            next(connection for connection in browsers.connections if connection.writer is readonly)
        )
        control = await browsers.control("thread-a", state.session_id, human=True)
        old_password = browsers.desktop.password
        input_reader, writable, _ = await rfb(browsers, "thread-a", state.session_id, control.control_token)
        point = await runtime.evaluate(
            page,
            """(()=>{const r=document.querySelector('input').getBoundingClientRect();
            window.nativePointer=false;addEventListener('pointerdown',()=>window.nativePointer=true,{once:true});
            return {x:screenX+r.x+r.width/2,y:screenY+outerHeight-innerHeight+r.y+r.height/2}})()""",
        )
        if browsers.desktop.desktop.kind == "wayvnc":
            from a13n_harness_ui.browser_display import sway_request

            def nodes(node):
                yield node
                for child in [*node.get("nodes", []), *node.get("floating_nodes", [])]:
                    yield from nodes(child)

            identity = await browsers.native_window(page)
            tree = await sway_request(browsers.desktop.env, 4)
            bounds = next(node["rect"] for node in nodes(tree) if node["id"] == identity)
            # Wayland screen coordinates do not expose compositor placement;
            # Chromium's outer size also includes invisible CSD shadows.
            left, top, outer_height = await runtime.evaluate(page, "[screenX,screenY,outerHeight]")
            point["x"] += bounds["x"] - left
            point["y"] += bounds["y"] + bounds["height"] - top - outer_height
        await wait_input_paint(input_reader, writable, point)
        writable.write(
            struct.pack(">BBHH", 5, 1, round(point["x"]), round(point["y"]))
            + struct.pack(">BBHH", 5, 0, round(point["x"]), round(point["y"]))
        )
        await writable.drain()
        await wait_value(runtime, page, "window.nativePointer", True)
        await wait_value(runtime, page, "document.activeElement.name", "username")
        key(writable, ord("a"))
        await writable.drain()
        await wait_value(runtime, page, "document.querySelector('input').value", "a")
        with pytest.raises(HarnessUiError, match="user controls"):
            await backend.observe()
        await browsers.control("thread-a", state.session_id, human=False)
        assert writable.is_closing()
        if old_password:
            with pytest.raises(PermissionError):
                await rfb(browsers, "thread-a", state.session_id, native_password=old_password)
        await backend.observe()
        await runtime.evaluate(page, "document.cookie='login=retained; Max-Age=3600; Path=/'")
        other = await browsers.enable("thread-b")
        await browsers.backend("thread-b", other.session_id).navigate(site)
        second = browsers.bindings["thread-b"].page
        assert (await runtime.window(page))["windowId"] != (await runtime.window(second))["windowId"]
        assert await runtime.evaluate(second, "document.cookie") == "login=retained"
        async with backend.operation():
            async with asyncio.timeout(1):
                async with browsers.backend("thread-b", other.session_id).operation():
                    pass
        reset = await browsers.reset("thread-a", state.session_id)
        assert reset.session_id != state.session_id
        assert browsers.bindings["thread-b"].page is second
        assert await runtime.evaluate(second, "document.title") == "Native VNC fixture"
        control = await browsers.control("thread-a", reset.session_id, human=True)
        if browsers.state("thread-a").resize_available:
            sized = await browsers.resize(
                "thread-a",
                BrowserViewportRequest(
                    session_id=reset.session_id,
                    control_token=control.control_token,
                    preset="tablet",
                ),
            )
            assert sized.viewport == "tablet"
            await wait_value(runtime, browsers.bindings["thread-a"].page, "[innerWidth,innerHeight]", [768, 1024])
        else:
            with pytest.raises(HarnessUiError, match="resizing is unavailable"):
                await browsers.resize(
                    "thread-a",
                    BrowserViewportRequest(
                        session_id=reset.session_id, control_token=control.control_token, preset="tablet"
                    ),
                )
            assert browsers.state("thread-a").viewport == "desktop"
        await browsers.control("thread-a", reset.session_id, human=False)
        stopped = await browsers.stop("thread-a", reset.session_id)
        assert stopped.status == "stopped" and browsers.runtime is runtime
        assert await runtime.evaluate(second, "document.title") == "Native VNC fixture"
        await browsers.close()
        fresh = await browsers.enable("after-restart")
        await browsers.backend("after-restart", fresh.session_id).navigate(site)
        assert (
            await browsers.runtime.evaluate(browsers.bindings["after-restart"].page, "document.cookie")
            == "login=retained"
        )
        # Core actionability brings an offscreen target into view and focuses it.
        backend = browsers.backend("after-restart", fresh.session_id)
        runtime = browsers.runtime
        page = browsers.bindings["after-restart"].page
        await runtime.evaluate(page, "document.querySelector('input').style.marginTop='1500px'")
        observation = await backend.observe()
        target = next(element.target for element in observation.elements if element.description == "Message")
        result = await backend.interact(
            Click(kind="click", target=Ref(kind="ref", ref=target)), revision=observation.revision
        )
        assert json.loads(result)["dispatch"] == "dispatched"
        await wait_value(runtime, page, "scrollY>0 && document.activeElement.name==='username'", True)
    finally:
        await browsers.close()


async def test_thread_tabs_concurrent_actions_and_human_selection(
    tmp_path, site, browser_configuration, request, monkeypatch
):
    browsers = HostBrowsers(
        root=tmp_path / "browser",
        available=True,
        authorize=AsyncMock(),
        configuration=browser_configuration,
        no_sandbox=request.config.getoption("--browser-no-sandbox"),
    )
    try:
        a = await browsers.enable("a")
        one = browsers.backend("a", a.session_id)
        await one.navigate(site)
        b = await browsers.enable("b")
        two = browsers.backend("b", b.session_id)
        await two.navigate(site)
        runtime = browsers.runtime
        first = browsers.bindings["a"].page
        second = browsers.bindings["b"].page
        assert runtime and first and second
        original_window = (await runtime.window(first))["windowId"]
        observed_a, observed_b = await asyncio.gather(one.observe(), two.observe())
        field_a = next(item.target for item in observed_a.elements if item.description == "Message")
        field_b = next(item.target for item in observed_b.elements if item.description == "Message")
        rejected = json.loads(
            await two.interact(
                Fill(kind="fill", target=Ref(kind="ref", ref=field_a), text="wrong thread"),
                revision=observed_a.revision,
            )
        )
        assert rejected["dispatch"] == "not_dispatched"
        assert "expired" in rejected["error"]
        assert await runtime.evaluate(second, "document.querySelector('input').value") == ""
        waiting = asyncio.create_task(one.wait(TextCondition(kind="text", text="A ready")))
        try:
            await asyncio.wait_for(
                two.interact(
                    Fill(kind="fill", target=Ref(kind="ref", ref=field_b), text="B working"),
                    revision=observed_b.revision,
                ),
                3,
            )
            assert not waiting.done()
            assert await runtime.evaluate(second, "document.querySelector('input').value") == "B working"
            assert await runtime.evaluate(second, "document.hasFocus()")
            await runtime.evaluate(first, "document.querySelector('output').textContent='A ready'")
            await asyncio.wait_for(waiting, 3)
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        observed_a = await one.observe()
        button = next(item.target for item in observed_a.elements if item.description == "Sign in")
        await asyncio.wait_for(
            one.interact(Click(kind="click", target=Ref(kind="ref", ref=button)), revision=observed_a.revision), 3
        )
        assert await runtime.evaluate(first, "document.querySelector('output').textContent") == "Signed in"
        # Chromium may move native focus on CDP clicks. Subsequent page-targeted
        # input still belongs to B rather than whichever window has native focus.
        observed_b = await two.observe()
        field_b = next(item.target for item in observed_b.elements if item.description == "Message")
        original_send = runtime.send
        viewer_accesses = 0

        async def send(method, params=None, page=None):
            nonlocal viewer_accesses
            if method.startswith("Input."):
                # Opening A's read-only viewer during B's dispatch changes the
                # native foreground, but must not redirect page-targeted input.
                access = await browsers.access("a", BrowserAccessRequest(session_id=a.session_id))
                assert not access.control
                viewer_accesses += 1
            return await original_send(method, params, page)

        with monkeypatch.context() as patch:
            patch.setattr(runtime, "send", send)
            result = await two.interact(
                Fill(kind="fill", target=Ref(kind="ref", ref=field_b), text="B still working"),
                revision=observed_b.revision,
            )
        assert json.loads(result)["verification"] == "passed"
        assert viewer_accesses > 0
        assert await runtime.evaluate(first, "document.querySelector('input').value") == ""
        assert await runtime.evaluate(second, "document.querySelector('input').value") == "B still working"
        observed_b = await two.observe()
        button = next(item.target for item in observed_b.elements if item.description == "Sign in")
        await asyncio.wait_for(
            two.interact(Click(kind="click", target=Ref(kind="ref", ref=button)), revision=observed_b.revision), 3
        )
        assert await runtime.evaluate(second, "document.querySelector('output').textContent") == "Signed in"
        tabs = await one.tabs("new", url=site)
        assert len(tabs) == 2 and all(item.target != second.target for item in tabs)
        created = browsers.bindings["a"].page
        assert (await runtime.window(created))["windowId"] == original_window
        await one.tabs("select", target=first.target)
        await one.tabs("select", target=created.target)
        with pytest.raises(BrowserError, match="not owned"):
            await one.tabs("select", target=second.target)
        await browsers.control("a", a.session_id, human=True)
        with pytest.raises(HarnessUiError, match="user controls"):
            await one.tabs("list")
        await runtime.activate(first)  # The human changes the native selected tab.
        await browsers.control("a", a.session_id, human=False)
        assert browsers.bindings["a"].page is first
        await one.observe()
        await one.tabs("close", target=created.target)
        assert len(await one.tabs("list")) == 1
        assert browsers.bindings["b"].page is second
        await one.tabs("close", target=first.target)
        assert browsers.state("a").status == "ready"
        assert await one.tabs("list") == ()
        scope = browsers.bindings["a"].scope
        await one.tabs("new", url=site)
        assert browsers.bindings["a"].scope is scope
        assert browsers.state("a").session_id == a.session_id
        assert await runtime.evaluate(second, "document.querySelector('input').value") == "B still working"
    finally:
        await browsers.close()


@pytest.mark.parametrize("preset,minimum", [("tablet", (768, 1024)), ("phone", (390, 844))])
async def test_saved_viewport_survives_window_lifecycle(tmp_path, browser_configuration, request, preset, minimum):
    host = HostBrowsers(
        root=tmp_path / "browser",
        available=True,
        authorize=AsyncMock(),
        configuration=browser_configuration,
        no_sandbox=request.config.getoption("--browser-no-sandbox"),
    )
    try:
        state = await host.enable("a")
        state = await host.start("a", state.session_id)
        if not state.resize_available:
            pytest.skip("Viewport presets require RandR")
        second = await host.enable("b")
        await host.start("b", second.session_id)
        control = await host.control("a", state.session_id, human=True)
        await host.resize(
            "a",
            BrowserViewportRequest(
                session_id=state.session_id,
                control_token=control.control_token,
                preset=preset,
            ),
        )
        await host.control("a", state.session_id, human=False)
        runtime = host.runtime
        other = host.bindings["b"].page
        assert runtime and other

        async def check():
            page = host.bindings["a"].page
            assert page and host.state("a").viewport == preset
            width, height = await runtime.evaluate(page, "[innerWidth,innerHeight]")
            assert width >= minimum[0] and height == minimum[1]
            if preset == "tablet":
                assert width == minimum[0]
            assert other.target in runtime.pages

        await check()
        state = await host.reset("a", state.session_id)
        await check()
        state = await host.stop("a", state.session_id)
        state = await host.start("a", state.session_id, explicit=True)
        await check()
        backend = host.backend("a", state.session_id)
        for tab in await backend.tabs("list"):
            await backend.tabs("close", target=tab.target)
        await backend.tabs("new")
        await check()
    finally:
        await host.close()


async def test_repeated_presets_with_another_maximized_thread(tmp_path, browser_configuration, request):
    host = HostBrowsers(
        root=tmp_path / "browser",
        available=True,
        authorize=AsyncMock(),
        configuration=browser_configuration,
        no_sandbox=request.config.getoption("--browser-no-sandbox"),
    )
    try:
        state = await host.enable("a")
        state = await host.start("a", state.session_id)
        assert host.desktop
        if not state.resize_available or not host.desktop.desktop.window_manager:
            pytest.skip("Repeated native presets require RandR and a window manager")
        second = await host.enable("b")
        await host.start("b", second.session_id)
        runtime = host.runtime
        page = host.bindings["a"].page
        other = host.bindings["b"].page
        assert runtime and page and other, (
            host.state("a").error,
            [(name, process.returncode) for process, name in host.desktop.processes],
        )
        async with asyncio.timeout(5):
            while (await runtime.window(other))["bounds"]["windowState"] != "maximized":
                await asyncio.sleep(0.025)
        control = await host.control("a", state.session_id, human=True)
        screens = {}
        for cycle in range(6):
            for preset in ("desktop", "desktop", "tablet", "desktop", "phone", "laptop"):
                await host.resize(
                    "a",
                    BrowserViewportRequest(
                        session_id=state.session_id, control_token=control.control_token, preset=preset
                    ),
                )
                # Read the X server's geometry, not renderer-cached screen.width.
                match = re.search(r"current (\d+) x (\d+)", await host.desktop.randr("--current"))
                assert match
                dimensions = tuple(map(int, match.groups()))
                if cycle == 0:
                    screens[preset] = dimensions
                else:
                    assert dimensions == screens[preset], (cycle, preset, screens, dimensions)
                if preset == "tablet":
                    assert await runtime.evaluate(page, "[innerWidth,innerHeight]") == [768, 1024]
                assert other.target in runtime.pages
    finally:
        await host.close()


async def test_saved_password_autofill_after_restart_without_model_secret_values(
    tmp_path, site, browser_configuration, request
):
    from a13n_harness.capabilities import BrowserUseCapability

    host = HostBrowsers(
        root=tmp_path / "browser",
        available=True,
        authorize=AsyncMock(),
        configuration=browser_configuration,
        no_sandbox=request.config.getoption("--browser-no-sandbox"),
    )
    try:
        state = await host.enable("seed")
        await host.backend("seed", state.session_id).navigate(site)
        runtime = host.runtime
        page = host.bindings["seed"].page
        assert runtime and page
        await host.control("seed", state.session_id, human=True)
        # Prepare only fictional credentials in the disposable profile. Raw CDP
        # drives the human-owned browser settings here, never a model-visible tool.
        await runtime.navigate(page, "chrome://password-manager/passwords")

        async def named(words):
            last = ""
            try:
                async with asyncio.timeout(8):
                    while True:
                        observation = await runtime.observe(page)
                        last = observation.text
                        match = next(
                            (
                                item
                                for item in observation.elements
                                if any(word in item.description.lower() for word in words)
                            ),
                            None,
                        )
                        if match:
                            return match.target, observation.revision
                        await asyncio.sleep(0.05)
            except TimeoutError:
                pytest.fail("Password-manager control missing: " + last)

        target, revision = await named(("add", "添加"))
        await runtime.element(page, target, revision)
        for words, text in (
            (("website", "site", "网站"), site),
            (("username", "用户名"), "fictional-user"),
            (("password", "密码"), "fictional-password"),
        ):
            target, revision = await named(words)
            # Password field lookup excludes headings/buttons using its actual DOM type.
            if words[0] == "password":
                observation = await runtime.observe(page)
                for element in observation.elements:
                    node = page.elements[element.target]
                    resolved = await runtime.send("DOM.resolveNode", {"backendNodeId": node.backend}, page)
                    identity = resolved["object"]["objectId"]
                    try:
                        answer = await runtime.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": identity,
                                "returnByValue": True,
                                "functionDeclaration": "function(){return this.type==='password'}",
                            },
                            page,
                        )
                        if answer.get("result", {}).get("value"):
                            target, revision = element.target, observation.revision
                            break
                    finally:
                        await runtime.send("Runtime.releaseObject", {"objectId": identity}, page)
            await runtime.element(page, target, revision)
            await runtime.send("Input.insertText", {"text": text}, page)
        target, revision = await named(("save", "保存"))
        await runtime.element(page, target, revision)
        await host.control("seed", state.session_id, human=False)
        await host.close()
        import sqlite3

        with sqlite3.connect(host.root / "Default" / "Login Data") as saved:
            rows = saved.execute("select origin_url, username_value, length(password_value) from logins").fetchall()
        assert any(row[1] == "fictional-user" and row[2] > 0 for row in rows), rows
        for thread in ("thread-a", "thread-b"):
            current = await host.enable(thread)
            backend = host.backend(thread, current.session_id)
            tools = BrowserUseCapability(backend).get_toolset().tools

            async def call(name, _tools=tools, **kwargs):
                schema = _tools[name].function_schema
                return await schema.call(schema.validator.validate_python(kwargs), None)

            await call("browser_navigate", url=site)
            snapshot = await call("browser_observe")
            assert "fictional-password" not in snapshot
            ref = re.search(r'\[(e\d+)\] textbox: "Message"', snapshot).group(1)
            await call("browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": ref}})
            active = host.bindings[thread].page
            # This synthetic form prevents submission; retry native suggestion keys
            # until the asynchronous password-store query has produced its result.
            async with asyncio.timeout(5):
                while True:
                    await call("browser_act", action={"kind": "press", "key": "ArrowDown"})
                    await call("browser_act", action={"kind": "press", "key": "Enter"})
                    matched = await host.runtime.evaluate(
                        active,
                        "document.querySelector('[name=username]').value==='fictional-user'&&document.querySelector('[type=password]').value==='fictional-password'",
                    )
                    if matched:
                        break
                    await asyncio.sleep(0.05)
            assert "fictional-password" not in await call("browser_observe")
        await host.runtime.evaluate(active, "document.cookie='login=retained;max-age=3600;path=/'")
        process = host.process
        await host.close()
        # Exit immediately after autofill, with no navigation to dismiss native
        # password UI. A successful Browser.close response alone does not prove
        # that Chromium exited normally or persisted the profile.
        assert process and process.returncode == 0
        current = await host.enable("after-autofill-restart")
        await host.backend("after-autofill-restart", current.session_id).navigate(site)
        assert (
            await host.runtime.evaluate(host.bindings["after-autofill-restart"].page, "document.cookie")
            == "login=retained"
        )
    finally:
        await host.close()
