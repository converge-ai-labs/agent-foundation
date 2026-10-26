"""Opt-in real CDP tool workflows, including same-process and cross-site iframes."""

from __future__ import annotations

import asyncio
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser.actions import Press
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

from .test_browser_cdp_native import native_browser as native_browser

pytestmark = pytest.mark.anyio


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
                if route.path == "/nested":
                    depth = int(parse_qs(route.query).get("depth", ["0"])[0])
                    mixed = parse_qs(route.query).get("mixed", ["0"])[0]
                    if depth == 3:
                        body = b"""<!doctype html><div id=host></div><script>
                        const root=host.attachShadow({mode:'open'});
                        root.innerHTML='<input aria-label="Nested name"><button>Nested submit</button>';
                        root.querySelector('button').onclick=()=>top.postMessage(root.querySelector('input').value,'*');
                        </script>"""
                    else:
                        host = "localhost" if mixed == "1" or depth % 2 == 0 else "127.0.0.1"
                        body = f"""<!doctype html><style>
                        body{{height:1600px}}iframe{{display:block;width:{600 - depth * 100}px;height:300px;
                        margin-top:{500 if depth == 0 else 60}px;border:4px solid;
                        transform:scale({0.8 if depth == 1 else 0.9});transform-origin:top left}}
                        </style><iframe src="http://{host}:{self.server.server_port}/nested?depth={depth + 1}&mixed={mixed}"></iframe>
                        <output id=result>Ready</output><script>addEventListener('message',e=>result.textContent=e.data)</script>""".encode()
                elif route.path == "/frame":
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


async def test_frame_input_then_click_does_not_scroll_during_hover_verification(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        await call("browser_navigate", url=workflow_site)
        await backend.browser.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 780, "height": 437, "deviceScaleFactor": 1, "mobile": False},
            backend.page,
        )
        await backend.browser.evaluate(
            backend.page,
            """document.querySelector('iframe').style.marginTop='190.59375px';
            for(const f of document.querySelectorAll('iframe')){f.style.height='100px';f.style.marginBottom='12px';}
            """,
        )
        calls = []
        original_send = backend.browser.send

        async def traced(method, params=None, page=None):
            calls.append((method, params or {}))
            return await original_send(method, params, page)

        backend.browser.send = traced
        for _ in range(3):
            for label in ("Same", "Cross"):
                for kind, extra in (("fill", {"text": label}), ("type", {"text": "!"})):
                    result = json.loads(
                        await call(
                            "browser_act",
                            action={"kind": kind, "target": {"kind": "locator", "name": label + " input"}, **extra},
                        )
                    )
                    assert result["dispatch"] == "dispatched", result
                calls.clear()
                result = json.loads(
                    await call(
                        "browser_act",
                        action={"kind": "click", "target": {"kind": "locator", "name": label + " button"}},
                    )
                )
                assert result["dispatch"] == "dispatched", result
                hover_index = next(
                    i
                    for i, (method, params) in enumerate(calls)
                    if method == "Input.dispatchMouseEvent" and params["type"] == "mouseMoved"
                )
                assert not any(method == "DOM.scrollIntoViewIfNeeded" for method, _ in calls[hover_index:])
                await call("browser_wait", condition={"kind": "text", "text": label + " clicked"})
                assert await backend.browser.evaluate(backend.page, "result.textContent") == label + " clicked"


async def test_frame_dialog_scroll_and_host_file_tools(native_browser, workflow_site, tmp_path):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        async def ref(name):
            observation = ""
            try:
                async with asyncio.timeout(5):
                    while True:
                        observation = await call("browser_observe", name=name)
                        matches = [
                            line.strip().split("]")[0][1:]
                            for line in observation.splitlines()
                            if line.strip().startswith("[e")
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
        assert '"dispatch": "dispatched"' in await call(
            "browser_act",
            action={"kind": "fill", "target": {"kind": "ref", "ref": target}, "text": "fictional-input-secret"},
        )
        assert (
            await backend.browser.evaluate(backend.page, "document.querySelector('[type=password]').value")
            == "fictional-input-secret"
        )
        assert "fictional-input-secret" not in await call("browser_observe")
        for label in ("Same", "Cross"):
            target = await ref(label + " button")
            response = await call("browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": target}})
            assert json.loads(response)["dispatch"] == "dispatched", response
            async with asyncio.timeout(3):
                while await result() != label + " clicked":
                    await asyncio.sleep(0.025)
            target = await ref(label + " input")
            assert '"dispatch": "dispatched"' in await call(
                "browser_act",
                action={"kind": "fill", "target": {"kind": "ref", "ref": target}, "text": label + " filled"},
            )
        protected = await ref("Cross button")
        await backend.browser.evaluate(
            backend.page,
            "(()=>{const d=document.createElement('div');d.id='cover';d.style='position:fixed;inset:0;background:#fff;z-index:999';document.body.append(d)})()",
        )
        assert "covered" in await call(
            "browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": protected}}
        )
        await backend.browser.evaluate(backend.page, "document.getElementById('cover').remove()")
        epoch = backend.page.epoch
        await backend.browser.evaluate(backend.page, "document.querySelectorAll('iframe')[1].src+='&reload=1'")
        async with asyncio.timeout(3):
            while backend.page.epoch == epoch:
                await asyncio.sleep(0.025)
        assert "expired" in await call(
            "browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": protected}}
        )
        await ref("Cross button")
        region = await ref("Scroll region")
        scroll = json.loads(
            json.loads(
                await call("browser_act", action={"kind": "scroll", "target": {"kind": "ref", "ref": region}, "y": 250})
            )["detail"].split(": ", 1)[1]
        )
        assert scroll["after"][1] == 250
        scroll = json.loads(
            json.loads(await call("browser_act", action={"kind": "scroll", "y": 200}))["detail"].split(": ", 1)[1]
        )
        assert scroll["after"][1] > scroll["before"][1]
        for button, action, text, expected in (
            ("Cross alert", "accept", "", None),
            ("Alert", "accept", "", None),
            ("Confirm", "dismiss", "", "dismissed"),
            ("Prompt", "accept", "fictional answer", "fictional answer"),
        ):
            target = await ref(button)
            response = await call("browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": target}})
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
        assert "selected" in await call("browser_files", action="upload", ref=target, paths=[str(upload)])
        assert await result() == upload.name
        target = await ref("Cross upload")
        assert "selected" in await call("browser_files", action="upload", ref=target, paths=[str(upload)])
        async with asyncio.timeout(3):
            while await result() != upload.name:
                await asyncio.sleep(0.025)
        await backend.browser.configure_downloads(str(tmp_path))
        target = await ref("Download fixture")
        await call("browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": target}})
        async with asyncio.timeout(5):
            while True:
                downloads = json.loads(await call("browser_files", action="downloads"))["downloads"]
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

        assert "Observe" in await backend.interact(Press(kind="press", key="Enter"))
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
        assert '"verification": "passed"' in await navigate.call({"url": "chrome://settings"}, None)
        assert backend.page.url.startswith("chrome://settings")
        assert '"verification": "passed"' in await navigate.call({"url": document.as_uri()}, None)
        assert "Local browser document" in await tools["browser_observe"].function_schema.call({}, None)


@pytest.mark.parametrize("native_browser", ["extension"], indirect=True)
async def test_installed_extension_content_and_native_action_popup(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate(workflow_site)
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


async def test_semantic_observation_and_fill_never_write_to_previous_focus(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        async def ref(name):
            observation = await call("browser_observe", name=name)
            return next(
                line.strip().split("]")[0][1:] for line in observation.splitlines() if line.strip().startswith("[e")
            )

        await call("browser_navigate", url=workflow_site)
        empty = await call("browser_observe", name="Name")
        assert "value=" not in empty
        original = await ref("Name")
        assert original == await ref("Name")
        assert '"dispatch": "dispatched"' in await call(
            "browser_act", action={"kind": "fill", "target": {"kind": "ref", "ref": original}, "text": "Ada"}
        )
        assert 'value="Ada"' in await call("browser_observe", name="Name")
        forms = json.loads(await call("browser_read", kind="forms"))
        assert next(item for item in forms["items"] if item["name"] == "Name")["value"] == "Ada"
        assert next(item for item in forms["items"] if item["type"] == "password")["value"] == "[redacted]"
        assert forms["next_frame"] == 1
        assert "Same input" in await call("browser_read", kind="forms", frame_index=1)
        assert "Cross input" in await call("browser_read", kind="forms", frame_index=2)
        links = json.loads(await call("browser_read", kind="links"))
        assert links["items"][0]["url"] == workflow_site + "download"
        # Reading other frames and representations never revokes an observed target.
        assert '"dispatch": "dispatched"' in await call(
            "browser_act", action={"kind": "fill", "target": {"kind": "ref", "ref": original}, "text": "Ada"}
        )
        # A reference determines the actual document even when frame_index is omitted.
        cross = await ref("Cross input")
        referenced = json.loads(await call("browser_read", kind="forms", ref=cross))
        assert referenced["frame_index"] == 2
        assert referenced["items"][0]["name"] == "Cross input"
        await backend.browser.evaluate(
            backend.page,
            """(()=>{
            document.body.insertAdjacentHTML('afterbegin', `
                <input aria-label="Read only" readonly value="locked">
                <fieldset disabled><input aria-label="Disabled field" value="locked"></fieldset>
                <input aria-label="Redirect focus" onfocus="document.querySelector('[aria-label=Name]').focus()">
                <button aria-label="Partially covered" style="position:fixed;left:0;top:0;width:300px;height:40px" onclick="result.textContent='side clicked'">Wide button</button>
                <div style="position:fixed;left:130px;top:0;width:40px;height:40px;z-index:99"></div>`);
        })()""",
        )
        for label in ("Read only", "Disabled field", "Redirect focus"):
            target = await ref(label)
            response = await call(
                "browser_act",
                action={"kind": "fill", "target": {"kind": "ref", "ref": target}, "text": "wrong target text"},
            )
            assert "completed" not in response, response
            assert (
                await backend.browser.evaluate(backend.page, "document.querySelector('[aria-label=Name]').value")
                == "Ada"
            )
        target = await ref("Partially covered")
        assert '"dispatch": "dispatched"' in await call(
            "browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": target}}
        )
        assert await backend.browser.evaluate(backend.page, "result.textContent") == "side clicked"
        # Paging and filtering apply to the complete semantic set, not an initial truncation.
        await backend.browser.evaluate(
            backend.page,
            """(()=>{
            document.body.innerHTML='<section aria-label="Long form">'+Array.from({length:220},(_,i)=>`<button>Choice ${i}</button>`).join('')+'</section>';
        })()""",
        )
        full = await call("browser_observe", role="button", limit=10)
        assert "Rows: 10 of 220" in full and "offset=10" in full
        last = await ref("Choice 219")
        assert last.startswith("e")
        region = await ref("Long form")
        scoped = await call("browser_observe", ref=region, role="button", offset=219, limit=1)
        assert 'button: "Choice 219"' in scoped


async def test_typed_form_actions_verify_local_state(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        async def act(kind, name=None, **kwargs):
            action = {"kind": kind, **kwargs}
            if name:
                action["target"] = {"kind": "locator", "name": name}
            return json.loads(await call("browser_act", action=action))

        await call("browser_navigate", url=workflow_site)
        await call("browser_wait", condition={"kind": "text", "text": "Cross button"})
        await backend.browser.evaluate(
            backend.page,
            """(()=>{
            document.body.insertAdjacentHTML('afterbegin', `<select aria-label="Plan"><option value="basic">Basic</option><option value="pro">Professional</option></select>
                <input type="checkbox" aria-label="Accept">
                <input aria-label="Read only" readonly value="locked">
                <button>Duplicate</button><button>Duplicate</button>
                <table><caption>Results</caption><tr><th>Plan</th><th>Cost</th></tr><tr><td>Pro</td><td>20</td></tr></table>`);
        })()""",
        )
        result = await act("fill", "Name", text="Ada")
        assert result["dispatch"] == "dispatched" and result["verification"] == "passed", result
        rejected = await act("fill", "Read only", text="wrong")
        assert rejected["dispatch"] == "not_dispatched", rejected
        assert (
            await backend.browser.evaluate(backend.page, "document.querySelector('[aria-label=Name]').value") == "Ada"
        )
        result = await act(
            "select",
            values=["Professional"],
            by="label",
            target={"kind": "locator", "role": "combobox", "name": "Plan"},
        )
        assert result["verification"] == "passed" and result["state"]["selected"] == ["pro"], result
        result = await act("check", "Accept")
        assert result["verification"] == "passed" and result["state"]["checked"] is True, result
        result = await act("check", "Accept")
        assert result["dispatch"] == "not_dispatched" and result["verification"] == "passed", result
        result = await act("check", "Accept", checked=False)
        assert result["verification"] == "passed" and result["state"]["checked"] is False, result
        result = await act("click", "Duplicate")
        assert result["dispatch"] == "not_dispatched" and "2 targets" in result["error"], result
        result = await act("press", "Name", key="Control+A")
        assert result["dispatch"] == "dispatched", result
        result = await act("type", "Name", text="Grace+1")
        assert result["state"]["value"] == "Grace+1", result
        for label in ("Same input", "Cross input"):
            result = await act("fill", label, text="frame value")
            assert result["verification"] == "passed" and result["state"]["value"] == "frame value", result
        table = json.loads(await call("browser_read", kind="tables", limit=1))
        assert table["items"][0]["cells"][0]["header"] is True and table["next_offset"] == 1
        table = json.loads(await call("browser_read", kind="tables", offset=1))
        assert [cell["text"] for cell in table["items"][0]["cells"]] == ["Pro", "20"]


async def test_semantic_wait_preserves_refs_and_observes_future_controls(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools
        assert len(tools) == 9

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        await call("browser_navigate", url=workflow_site)
        await call("browser_wait", condition={"kind": "text", "text": "Cross button"})
        observed = await call("browser_observe", name="Name", role="textbox")
        ref = next(line.strip().split("]")[0][1:] for line in observed.splitlines() if "[e" in line)
        await backend.browser.evaluate(
            backend.page, "setTimeout(()=>document.body.insertAdjacentHTML('afterbegin','<button>Later</button>'),150)"
        )
        result = json.loads(
            await call(
                "browser_wait",
                condition={"kind": "element", "target": {"kind": "locator", "role": "button", "name": "Later"}},
                timeout=2,
            )
        )
        assert result["verification"] == "passed", result
        result = json.loads(
            await call(
                "browser_act", action={"kind": "fill", "target": {"kind": "ref", "ref": ref}, "text": "Still valid"}
            )
        )
        assert result["verification"] == "passed", result
        result = json.loads(
            await call(
                "browser_wait",
                condition={"kind": "element", "target": {"kind": "locator", "name": "Never exists"}},
                timeout=0.2,
            )
        )
        assert result["verification"] == "timed_out", result
        await backend.browser.evaluate(backend.page, "document.querySelector('button').remove()")
        result = json.loads(
            await call(
                "browser_wait",
                condition={"kind": "element", "target": {"kind": "locator", "name": "Later"}, "state": "hidden"},
            )
        )
        assert result["verification"] == "passed", result
        # A same-document replacement during hover must never redirect a click.
        await backend.browser.evaluate(
            backend.page,
            "document.body.innerHTML='<button onmouseenter=\"this.outerHTML=\\'<button onclick=&quot;document.body.dataset.clicked=1&quot;>Replacement</button>\\'\">Original</button>'",
        )
        result = json.loads(
            await call("browser_act", action={"kind": "click", "target": {"kind": "locator", "name": "Original"}})
        )
        assert result["dispatch"] == "not_dispatched" and "no click" in result["error"], result
        assert await backend.browser.evaluate(backend.page, "document.body.dataset.clicked") is None


async def test_navigation_reports_final_url_history_and_load_state(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def navigate(**kwargs):
            schema = tools["browser_navigate"].function_schema
            result = json.loads(await schema.call(schema.validator.validate_python(kwargs), None))
            assert result["verification"] == "passed", result
            assert result["dispatch"] == "dispatched", result
            return result

        result = await navigate(url=workflow_site)
        assert result["url"] == workflow_site and result["title"] == "Browser workflows"
        await navigate(url=workflow_site + "#second")
        assert (await navigate(action="back"))["url"] == workflow_site
        assert (await navigate(action="forward"))["url"] == workflow_site + "#second"
        assert (await navigate(action="reload"))["url"] == workflow_site + "#second"
        assert (await navigate(url=workflow_site + "frame?label=Other"))["url"].endswith("label=Other")
        assert (await navigate(action="back"))["url"] == workflow_site + "#second"


@pytest.mark.parametrize("device_scale", [1, 2])
async def test_screenshot_visual_click_crop_and_stale_viewport(native_browser, device_scale):
    from urllib.parse import quote

    from pydantic_ai import ModelRetry, ToolReturn

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.browser.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 800, "height": 600, "deviceScaleFactor": device_scale, "mobile": False},
            backend.page,
        )
        markup = """<style>body{margin:0;height:1600px}button{position:absolute;left:40px;top:40px;width:80px;height:40px}</style>
        <button onclick="document.body.dataset.clicks=Number(document.body.dataset.clicks||0)+1">Paint</button>"""
        await backend.navigate("data:text/html," + quote(markup))
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        observation = await call("browser_observe")
        ref = re.search(r'\[(e\d+)\] button: "Paint"', observation)[1]
        shot = await call("browser_screenshot", clip={"x": 20, "y": 20, "width": 200, "height": 100})
        assert isinstance(shot, ToolReturn)
        assert shot.content[0].data.startswith(b"\xff\xd8")
        assert shot.return_value == {"screenshot": "s1", "width_css": 200, "height_css": 100, "visual_action": True}
        await call("browser_read")
        outcome = json.loads(
            await call(
                "browser_act",
                action={"kind": "click", "target": {"kind": "visual", "screenshot": "s1", "x": 0.3, "y": 0.4}},
            )
        )
        assert outcome["dispatch"] == "dispatched", outcome
        assert await backend.browser.evaluate(backend.page, "document.body.dataset.clicks") == "1"
        with pytest.raises(ModelRetry, match="screenshot"):
            await call(
                "browser_act",
                action={"kind": "click", "target": {"kind": "visual", "screenshot": "s1", "x": 0.3, "y": 0.4}},
            )
        # Screenshot/read do not revoke semantic refs.
        assert (
            json.loads(await call("browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": ref}}))[
                "dispatch"
            ]
            == "dispatched"
        )
        assert await backend.browser.evaluate(backend.page, "document.body.dataset.clicks") == "2"
        shot = await call("browser_screenshot", full_page=True)
        assert isinstance(shot, ToolReturn), shot
        assert shot.return_value["height_css"] >= 1600
        assert not shot.return_value["visual_action"]
        shot = await call("browser_screenshot")
        assert isinstance(shot, ToolReturn), shot
        # Uncontrolled scroll is detected even without a navigation event.
        await backend.browser.evaluate(backend.page, "scrollTo(0,300)")
        outcome = json.loads(
            await call(
                "browser_act",
                action={
                    "kind": "click",
                    "target": {"kind": "visual", "screenshot": shot.return_value["screenshot"], "x": 0.1, "y": 0.1},
                },
            )
        )
        assert outcome["dispatch"] == "not_dispatched" and "Viewport changed" in outcome["error"]
        assert await backend.browser.evaluate(backend.page, "document.body.dataset.clicks") == "2"


async def test_filtered_subtree_pagination_preserves_root_reference(native_browser):
    from urllib.parse import quote

    from pydantic_ai import ModelRetry

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate(
            "data:text/html," + quote('<form aria-label="Actions"><button>First</button><button>Second</button></form>')
        )
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        observed = await call("browser_observe", role="form")
        root = re.search(r"\[(e\d+)\] form:", observed)[1]
        first = await call("browser_observe", ref=root, role="button", limit=1)
        assert 'button: "First"' in first and f"Subtree [{root}]" in first
        second = await call("browser_observe", ref=root, role="button", limit=1, offset=1)
        assert 'button: "Second"' in second and 'button: "First"' not in second
        assert "Second" in await call("browser_read", ref=root)
        await call("browser_navigate", url="about:blank")
        with pytest.raises(ModelRetry, match="reference"):
            await call("browser_observe", ref=root)


async def test_visual_double_click_stops_after_page_scroll(native_browser):
    from urllib.parse import quote

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate(
            "data:text/html,"
            + quote("""
            <style>body{height:2000px;margin:0}button{position:fixed;left:20px;top:20px;width:100px;height:60px}</style>
            <button onclick="document.body.dataset.clicks=Number(document.body.dataset.clicks||0)+1;window.scrollTo(0,300)">Move</button>
        """)
        )
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        await call("browser_observe")
        shot = await call("browser_screenshot", clip={"x": 20, "y": 20, "width": 100, "height": 60})
        result = json.loads(
            await call(
                "browser_act",
                action={
                    "kind": "click",
                    "count": 2,
                    "target": {"kind": "visual", "screenshot": shot.return_value["screenshot"], "x": 0.5, "y": 0.5},
                },
            )
        )
        assert result["dispatch"] == "dispatched", result
        actual = await backend.browser.evaluate(backend.page, "[document.body.dataset.clicks,scrollY]")
        assert "remaining clicks were not sent" in result.get("error", ""), (result, actual)
        assert actual == ["1", 300]


async def test_direct_popup_download_is_tracked_before_attachment(native_browser, workflow_site, tmp_path):
    from pathlib import Path

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        other = CdpBrowserBackend(backend.browser)
        await backend.browser.configure_downloads(str(tmp_path))
        await backend.navigate(workflow_site)
        await backend.browser.evaluate(
            backend.page,
            """
            document.body.innerHTML='<a href="/download" target="_blank">Popup download</a>';
        """,
        )
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, /, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        for count in range(1, 4):
            await call("browser_observe")
            result = json.loads(
                await call(
                    "browser_act",
                    action={
                        "kind": "click",
                        "target": {"kind": "locator", "role": "link", "name": "Popup download"},
                    },
                )
            )
            assert result["dispatch"] == "dispatched", result
            async with asyncio.timeout(5):
                while True:
                    downloads = json.loads(await call("browser_files", action="downloads"))["downloads"]
                    if len(downloads) == count and all(item["state"] == "completed" for item in downloads):
                        break
                    await asyncio.sleep(0.025)
            assert all(Path(item["path"]).read_bytes() == b"synthetic download" for item in downloads)
            assert json.loads(await other.downloads()) == {"downloads": [], "omitted": 0}
        await other.close()


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
async def test_nested_scaled_cross_site_frames_with_shadow_form(native_browser, workflow_site, background, mixed):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        tools = BrowserUseCapability(backend).get_toolset().tools

        async def call(name, **kwargs):
            schema = tools[name].function_schema
            return await schema.call(schema.validator.validate_python(kwargs), None)

        await call("browser_navigate", url=workflow_site + f"nested?mixed={int(mixed)}")
        waited = await call(
            "browser_wait",
            condition={"kind": "element", "target": {"kind": "locator", "name": "Nested name"}, "state": "visible"},
        )
        assert json.loads(waited)["verification"] == "passed", waited
        if background:
            foreground = await backend.browser.create()
            backend._claim(foreground.target)
            await backend.browser.activate(foreground)
            assert await backend.browser.evaluate(backend.page, "document.hidden") is True
        observation = await call("browser_observe")
        assert 'textbox: "Nested name"' in observation
        frames = list(backend.page.frames.values())
        assert len(frames) == 4
        assert len({frame.session for frame in frames}) == (2 if mixed else 4)
        for value in ("Ada", "Grace", "Lin"):
            result = json.loads(
                await call(
                    "browser_act",
                    action={"kind": "fill", "target": {"kind": "locator", "name": "Nested name"}, "text": value},
                )
            )
            assert result["verification"] == "passed", result
            result = json.loads(
                await call(
                    "browser_act",
                    action={"kind": "type", "target": {"kind": "locator", "name": "Nested name"}, "text": "!"},
                )
            )
            assert result["dispatch"] == "dispatched", result
            forms = await call("browser_read", kind="forms", frame_index=3)
            assert value + "!" in forms, (forms, result)
            result = json.loads(
                await call(
                    "browser_act", action={"kind": "click", "target": {"kind": "locator", "name": "Nested submit"}}
                )
            )
            assert result["dispatch"] == "dispatched", result
            await call("browser_wait", condition={"kind": "text", "text": value + "!"})
            assert await backend.browser.evaluate(backend.page, "result.textContent") == value + "!"


async def test_background_iframe_churn_preserves_main_frame_actions(native_browser, workflow_site):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate(workflow_site)
        await backend.browser.evaluate(
            backend.page,
            """
            document.body.innerHTML='<button onclick="this.dataset.clicks=+(this.dataset.clicks||0)+1">Stable</button>';
        """,
        )
        observed = await backend.observe()
        target = next(item.target for item in observed.elements if item.description == "Stable")
        await backend.browser.evaluate(
            backend.page,
            """
            window.churn=setInterval(()=>{
                const f=document.createElement('iframe');document.body.append(f);setTimeout(()=>f.remove(),60);
            },120);
        """,
        )
        try:
            from a13n_harness.capabilities.browser.actions import Click, Ref

            for _ in range(10):
                await asyncio.sleep(0.125)
                result = json.loads(
                    await backend.interact(
                        Click(kind="click", target=Ref(kind="ref", ref=target)), revision=observed.revision
                    )
                )
                assert result["dispatch"] == "dispatched" and not result["navigated"], result
            assert (
                await backend.browser.evaluate(backend.page, "document.querySelector('button').dataset.clicks") == "10"
            )
        finally:
            await backend.browser.evaluate(backend.page, "clearInterval(churn)")


async def test_uncommitted_iframe_download_is_owned(native_browser, workflow_site, tmp_path):
    from pathlib import Path

    from a13n_harness.capabilities.browser.actions import Click, Locator

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        other = CdpBrowserBackend(backend.browser)
        await backend.browser.configure_downloads(str(tmp_path))
        await backend.navigate(workflow_site)
        await backend.browser.evaluate(
            backend.page,
            """
            document.body.innerHTML='<button>Download in frame</button>';
            document.querySelector('button').onclick=()=>{
                const f=document.createElement('iframe');f.src='/download';document.body.append(f);
            };
        """,
        )
        result = json.loads(
            await backend.interact(Click(kind="click", target=Locator(kind="locator", name="Download in frame")))
        )
        assert result["dispatch"] == "dispatched" and not result["navigated"], result
        async with asyncio.timeout(5):
            while True:
                report = json.loads(await backend.downloads())
                if report["downloads"] and report["downloads"][0]["state"] == "completed":
                    break
                await asyncio.sleep(0.025)
        assert report["omitted"] == 0
        assert Path(report["downloads"][0]["path"]).read_bytes() == b"synthetic download"
        assert json.loads(await other.downloads()) == {"downloads": [], "omitted": 0}
        await other.close()


async def test_visual_and_element_wait_survive_unrelated_frame_churn(native_browser):
    from urllib.parse import quote

    from a13n_harness.capabilities.browser.actions import Click, Clip, ElementCondition, Locator, Ref, Visual

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate(
            "data:text/html,"
            + quote("""
            <style>body{margin:0}button{position:fixed;left:20px;top:20px;width:100px;height:60px}
            iframe{position:fixed;left:400px;top:200px;width:20px;height:20px}</style>
            <button onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">Stable</button>
        """)
        )
        observation = await backend.observe()
        ref = next(item.target for item in observation.elements if item.description == "Stable")
        await backend.browser.evaluate(
            backend.page,
            """
            window.churn=setInterval(()=>{
                document.querySelector('iframe')?.remove();
                document.body.appendChild(document.createElement('iframe'));
            },10)
        """,
        )
        for _ in range(5):
            shot = await backend.screenshot(clip=Clip(x=20, y=20, width=100, height=60))
            assert shot.data.startswith(b"\xff\xd8")
            # Leave enough time for several unrelated frame events after capture.
            await asyncio.sleep(0.05)
            result = json.loads(
                await backend.interact(
                    Click(kind="click", target=Visual(kind="visual", screenshot=shot.token, x=0.5, y=0.5))
                )
            )
            assert result["dispatch"] == "dispatched", result
        assert await backend.browser.evaluate(backend.page, "document.querySelector('button').dataset.clicks") == "5"
        for target in (Ref(kind="ref", ref=ref), Locator(kind="locator", role="button", name="Stable")):
            result = json.loads(
                await backend.wait(
                    ElementCondition(kind="element", target=target), revision=observation.revision, timeout=2
                )
            )
            assert result["verification"] == "passed", result
        await backend.browser.evaluate(backend.page, "clearInterval(window.churn)")


async def test_shift_chords_insert_us_keyboard_symbols(native_browser):
    from urllib.parse import quote

    from a13n_harness.capabilities.browser.actions import Locator

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate("data:text/html," + quote('<input aria-label="Symbols">'))
        target = Locator(kind="locator", role="textbox", name="Symbols")
        for key in ("1", "9", "0", "/", "a"):
            result = json.loads(await backend.interact(Press(kind="press", key="Shift+" + key, target=target)))
            assert result["dispatch"] == "dispatched", result
        assert await backend.browser.evaluate(backend.page, "document.querySelector('input').value") == "!()?A"


async def test_idle_background_screenshot_preserves_foreground(native_browser):
    from urllib.parse import quote

    from a13n_harness.capabilities.browser.actions import Click, Clip, Visual

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        other = CdpBrowserBackend(backend.browser)
        await other.tabs("new")
        tracking = """window.events=[];
            for(const name of ['visibilitychange','focus','blur'])
                addEventListener(name,()=>events.push(name));"""
        await backend.browser.send("Page.addScriptToEvaluateOnNewDocument", {"source": tracking}, backend.page)
        await backend.browser.evaluate(other.page, tracking)
        await asyncio.sleep(3.566)
        await backend.navigate(
            "data:text/html,"
            + quote("""
            <style>body{margin:0}button{position:absolute;left:20px;top:20px;width:100px;height:60px}</style>
            <button onclick="document.body.dataset.clicked='yes'">Target</button>
        """)
        )
        await asyncio.sleep(3.064)
        async with asyncio.timeout(3):
            shot = await backend.screenshot(clip=Clip(x=20, y=20, width=100, height=60))
        assert shot.data.startswith(b"\xff\xd8")
        assert (shot.width, shot.height) == (100, 60)
        assert await backend.browser.evaluate(backend.page, "[document.hidden,document.hasFocus(),events]") == [
            True,
            False,
            [],
        ]
        assert await backend.browser.evaluate(other.page, "[document.hidden,document.hasFocus(),events]") == [
            False,
            True,
            [],
        ]
        outcome = json.loads(
            await backend.interact(
                Click(kind="click", target=Visual(kind="visual", screenshot=shot.token, x=0.5, y=0.5))
            )
        )
        assert outcome["dispatch"] == "dispatched", outcome
        assert await backend.browser.evaluate(backend.page, "document.body.dataset.clicked") == "yes"
        await other.close()


async def test_screenshot_leaves_other_session_screencast_running(native_browser):
    from a13n_harness.capabilities.browser.cdp import Frame

    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        browser = backend.browser
        await backend.navigate("data:text/html,<body>Stream</body>")
        attached = await browser.send("Target.attachToTarget", {"targetId": backend.page.target, "flatten": True})
        observer = Frame(backend.page.target, attached["sessionId"])
        received = asyncio.Event()
        frames = []

        def on_frame(event, session):
            if session == observer.session:
                frames.append(event)
                browser.background(browser.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]}, observer))
                received.set()

        browser.client.register.Page.screencastFrame(on_frame)
        try:
            await browser.send("Page.enable", page=observer)
            await browser.send("Page.startScreencast", {"format": "jpeg", "maxWidth": 96, "maxHeight": 96}, observer)
            async with asyncio.timeout(3):
                await received.wait()
            await backend.screenshot()
            # Drain any in-flight frame, then force a new visible paint after
            # our session has stopped. The observer must still deliver it.
            await asyncio.sleep(0.2)
            received.clear()
            before = len(frames)
            await browser.evaluate(backend.page, "document.body.style.background='red'")
            async with asyncio.timeout(3):
                await received.wait()
            assert len(frames) > before
        finally:
            await browser.send("Page.stopScreencast", page=observer)
            await browser.send("Target.detachFromTarget", {"sessionId": observer.session})
