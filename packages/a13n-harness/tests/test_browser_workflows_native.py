"""Opt-in real CDP tool workflows, including same-process and cross-site iframes."""

from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser.cdp import CdpBrowserBackend

from .test_browser_cdp_native import native_browser as native_browser

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_BROWSER_REAL_TEST") != "1", reason="Opt-in native Chromium test"),
]


@pytest.fixture
async def workflow_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == "/download":
                body = b"synthetic download"
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="fixture.txt"')
                self.send_header("Content-Type", "text/plain")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                if route.path == "/frame":
                    label = parse_qs(route.query)["label"][0]
                    body = f'''<!doctype html><button onclick="parent.postMessage('{label} clicked','*')">{label} button</button><input aria-label="{label} input"><button onclick="alert('{label} frame alert')">{label} alert</button><input type="file" aria-label="{label} upload" onchange="parent.postMessage(this.files[0].name,'*')">'''.encode()
                else:
                    body = f"""<!doctype html><title>Browser workflows</title>
                    <style>body{{height:2600px}}iframe{{width:400px;height:120px;display:block;margin:20px;border:4px solid}}
                    [role=region]{{height:70px;width:300px;overflow:auto}}</style>
                    <input aria-label="Name"><input type="password" aria-label="Fixture password" autocomplete="off">
                    <button onclick="alert('fixture alert')">Alert</button>
                    <button onclick="result.textContent=confirm('fixture confirm')?'accepted':'dismissed'">Confirm</button>
                    <button onclick="result.textContent=prompt('fixture prompt','default')">Prompt</button>
                    <input type=file aria-label="Upload fixture" onchange="result.textContent=this.files[0].name">
                    <a href="/download" download>Download fixture</a>
                    <div role="region" aria-label="Scroll region"><div style="height:800px">Scrollable content</div></div>
                    <iframe src="/frame?label=Same"></iframe>
                    <iframe src="http://localhost:{self.server.server_port}/frame?label=Cross"></iframe>
                    <output id=result>Ready</output><script>addEventListener('message',e=>result.textContent=e.data)</script>""".encode()
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


async def test_frame_dialog_scroll_and_host_file_tools(native_browser, workflow_site, tmp_path):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, **kwargs):
            return await tools[name].function_schema.call(kwargs, None)

        async def ref(name):
            observation = ""
            try:
                async with asyncio.timeout(5):
                    while True:
                        observation = await call("browser_observe")
                        matches = [
                            line.split("]")[0][1:] for line in observation.splitlines() if line.endswith("] " + name)
                        ]
                        if matches:
                            return matches[0]
                        await asyncio.sleep(0.025)
            except TimeoutError:
                pytest.fail(f"Missing {name}: {observation}")

        async def result():
            return await backend.browser.evaluate(backend.page, "document.querySelector('output').textContent")

        await call("browser_navigate", url=workflow_site)
        target = await ref("Fixture password")
        assert "completed" in await call("browser_fill", ref=target, text="fictional-input-secret")
        assert (
            await backend.browser.evaluate(backend.page, "document.querySelector('[type=password]').value")
            == "fictional-input-secret"
        )
        assert "fictional-input-secret" not in await call("browser_observe")
        for label in ("Same", "Cross"):
            target = await ref(label + " button")
            response = await call("browser_click", ref=target)
            assert "completed" in response, response
            async with asyncio.timeout(3):
                while await result() != label + " clicked":
                    await asyncio.sleep(0.025)
            target = await ref(label + " input")
            assert "completed" in await call("browser_fill", ref=target, text=label + " filled")
        protected = await ref("Cross button")
        await backend.browser.evaluate(
            backend.page,
            "(()=>{const d=document.createElement('div');d.id='cover';d.style='position:fixed;inset:0;background:#fff;z-index:999';document.body.append(d)})()",
        )
        assert "covered" in await call("browser_click", ref=protected)
        await backend.browser.evaluate(backend.page, "document.getElementById('cover').remove()")
        epoch = backend.page.epoch
        await backend.browser.evaluate(backend.page, "document.querySelectorAll('iframe')[1].src+='&reload=1'")
        async with asyncio.timeout(3):
            while backend.page.epoch == epoch:
                await asyncio.sleep(0.025)
        assert "expired" in await call("browser_click", ref=protected)
        await ref("Cross button")
        region = await ref("Scroll region")
        scroll = json.loads((await call("browser_scroll", ref=region, y=250)).split(": ", 1)[1])
        assert scroll["after"][1] == 250
        scroll = json.loads((await call("browser_scroll", y=200)).split(": ", 1)[1])
        assert scroll["after"][1] > scroll["before"][1]
        for button, action, text, expected in (
            ("Cross alert", "accept", "", None),
            ("Alert", "accept", "", None),
            ("Confirm", "dismiss", "", "dismissed"),
            ("Prompt", "accept", "fictional answer", "fictional answer"),
        ):
            target = await ref(button)
            response = await call("browser_click", ref=target)
            assert "dispatched" in response or "completed" in response, (button, response)
            try:
                async with asyncio.timeout(3):
                    while backend.page.dialog is None:
                        await asyncio.sleep(0.025)
            except TimeoutError:
                pytest.fail(f"{button} did not open a dialog: {response}")
            assert "JavaScript dialog:" in await call("browser_dialog")
            assert "dialog" in (await call("browser_screenshot")).lower()
            assert "JavaScript dialog" in await call("browser_observe")
            assert "answered" in await call("browser_dialog", action=action, text=text)
            if expected:
                assert await result() == expected
        upload = tmp_path / "synthetic-upload.txt"
        upload.write_text("fictional fixture")
        target = await ref("Upload fixture")
        assert "selected" in await call("browser_upload", ref=target, paths=[str(upload)])
        assert await result() == upload.name
        target = await ref("Cross upload")
        assert "selected" in await call("browser_upload", ref=target, paths=[str(upload)])
        async with asyncio.timeout(3):
            while await result() != upload.name:
                await asyncio.sleep(0.025)
        await backend.browser.configure_downloads(str(tmp_path))
        target = await ref("Download fixture")
        await call("browser_click", ref=target)
        async with asyncio.timeout(5):
            while True:
                downloads = json.loads(await call("browser_downloads"))
                if downloads and downloads[0]["state"] == "completed":
                    break
                await asyncio.sleep(0.025)
        from pathlib import Path

        assert Path(downloads[0]["path"]).read_text() == "synthetic download"


async def test_new_tabs_stay_in_the_same_native_window(native_browser, workflow_site):
    endpoint, process = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        original = backend.page
        first_window = (await backend.browser.window(original))["windowId"]
        tabs = await backend.tabs("new", url=workflow_site + "frame?label=Tab")
        assert len(tabs) == 2 and backend.page is not original
        assert (await backend.browser.window(backend.page))["windowId"] == first_window
        from a13n_harness.capabilities.browser import BrowserError

        with pytest.raises(BrowserError, match="Observe"):
            await backend.act("press", value="Enter")
        await backend.tabs("select", target=original.target)
        assert backend.page is original
        await backend.tabs("close", target=original.target)
        assert len(await backend.tabs("list")) == 1
        assert process.returncode is None


async def test_browser_tools_navigate_chromium_settings_and_host_files(native_browser, tmp_path):
    endpoint, _ = native_browser
    document = tmp_path / "browser-fixture.html"
    document.write_text("<!doctype html><title>Host file fixture</title><h1>Local browser document</h1>")
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools
        navigate = tools["browser_navigate"].function_schema
        assert "completed" in await navigate.call({"url": "chrome://settings"}, None)
        assert backend.page.url.startswith("chrome://settings")
        assert "completed" in await navigate.call({"url": document.as_uri()}, None)
        assert "Local browser document" in await tools["browser_observe"].function_schema.call({}, None)


@pytest.mark.parametrize("native_browser", ["extension"], indirect=True)
async def test_installed_extension_content_and_native_action_popup(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.act("navigate", value=workflow_site)
        async with asyncio.timeout(5):
            while not await backend.browser.evaluate(
                backend.page, "document.documentElement.dataset.fixtureExtension==='ready'"
            ):
                await asyncio.sleep(0.025)
        infos = (await backend.browser.send("Target.getTargets"))["targetInfos"]
        worker = next(info for info in infos if info["type"] == "service_worker" and info["url"].endswith("/worker.js"))
        attached = await backend.browser.send(
            "Target.attachToTarget", {"targetId": worker["targetId"], "flatten": True}
        )
        from a13n_harness.capabilities.browser.cdp import Frame

        session = Frame("", attached["sessionId"])
        try:
            await backend.browser.activate(backend.page)
            opened = await backend.browser.send(
                "Runtime.evaluate",
                {
                    "expression": "chrome.action.openPopup().then(()=>true)",
                    "awaitPromise": True,
                    "returnByValue": True,
                    "userGesture": True,
                },
                session,
            )
            assert opened.get("result", {}).get("value"), opened
            async with asyncio.timeout(5):
                while True:
                    infos = (await backend.browser.send("Target.getTargets"))["targetInfos"]
                    popup = next((info for info in infos if info["url"].endswith("/popup.html")), None)
                    if popup:
                        break
                    await asyncio.sleep(0.025)
            page = await backend.browser.attach(popup["targetId"], popup["url"])
            assert "Synthetic extension popup" in (await backend.browser.observe(page)).text
        finally:
            await backend.browser.send("Target.detachFromTarget", {"sessionId": session.session})
