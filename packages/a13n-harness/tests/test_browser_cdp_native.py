"""Opt-in CDP integration without Harness UI, models or a display server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser.actions import Fill, Press, Ref
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend

pytestmark = pytest.mark.anyio
HTML = b"""<!doctype html><title>Harness CDP fixture</title><input aria-label="Message"><button onclick="document.querySelector('output').textContent='clicked'">Submit</button><output></output>"""


@pytest.fixture
async def native_browser(request, browser_executable):
    with TemporaryDirectory(prefix="harness-cdp-") as directory:
        arguments = [
            browser_executable,
            f"--user-data-dir={directory}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--headless=new",
            "--mute-audio",
            # Disposable test profiles must not depend on a desktop keyring.
            "--password-store=basic",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if request.config.getoption("--browser-no-sandbox"):
            arguments.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        if getattr(request, "param", None) == "extension":
            extension = Path(directory) / "synthetic-extension"
            extension.mkdir()
            (extension / "manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "Synthetic browser fixture",
                        "version": "1.0",
                        "background": {"service_worker": "worker.js"},
                        "action": {"default_popup": "popup.html"},
                        "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["content.js"]}],
                    }
                )
            )
            (extension / "content.js").write_text("document.documentElement.dataset.fixtureExtension='ready'")
            (extension / "worker.js").write_text("chrome.runtime.onInstalled.addListener(()=>{})")
            (extension / "popup.html").write_text("<!doctype html><h1>Synthetic extension popup</h1>")
            # Test fixture installation only; Harness never installs extensions.
            arguments.append("--load-extension=" + str(extension))
        # Let pytest capture browser diagnostics instead of hiding startup failures.
        process = await asyncio.create_subprocess_exec(*arguments, stdout=asyncio.subprocess.DEVNULL)
        try:
            endpoint = Path(directory) / "DevToolsActivePort"
            async with asyncio.timeout(20):
                while True:
                    if process.returncode is not None:
                        pytest.fail(f"Chromium exited before CDP startup (exit code {process.returncode})")
                    if endpoint.exists():
                        parts = endpoint.read_text().splitlines()
                        if len(parts) == 2:
                            break
                    await asyncio.sleep(0.05)
            yield f"http://127.0.0.1:{parts[0]}", process
        finally:
            if process.returncode is None:
                connection = None
                try:
                    connection = await Chromium.connect(f"http://127.0.0.1:{parts[0]}")
                    await connection.close_browser()
                    await asyncio.wait_for(process.wait(), 8)
                except Exception:
                    if process.returncode is None:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), 5)
                        except TimeoutError:
                            process.kill()
                            await process.wait()
                finally:
                    if connection:
                        await connection.close()


async def test_url_only_capability_works_and_disconnect_preserves_existing_browser(native_browser):
    endpoint, process = native_browser

    async def serve(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                + str(len(HTML)).encode()
                + b"\r\n\r\n"
                + HTML
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    existing = await Chromium.connect(endpoint)
    try:
        other = await existing.create()
        async with server, CdpBrowserBackend.connect(endpoint) as backend:
            target = backend.page.target
            tools = BrowserUseCapability(backend).get_toolset().tools

            async def call(name, /, **kwargs):
                schema = tools[name].function_schema
                return await schema.call(schema.validator.validate_python(kwargs), None)

            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            assert '"verification": "passed"' in await call("browser_navigate", url=url)
            assert "Harness CDP fixture" in await call("browser_observe")
            observation = await call("browser_observe", role="textbox", name="Message")
            field = next(line.strip().split("]")[0][1:] for line in observation.splitlines() if "[e" in line)
            assert '"dispatch": "dispatched"' in await call(
                "browser_act", action={"kind": "fill", "target": {"kind": "ref", "ref": field}, "text": "Harness only"}
            )
            assert (
                await backend.browser.evaluate(backend.page, "document.querySelector('input').value") == "Harness only"
            )
            observation = await call("browser_observe", role="button", name="Submit")
            submit = next(line.strip().split("]")[0][1:] for line in observation.splitlines() if "[e" in line)
            assert '"dispatch": "dispatched"' in await call(
                "browser_act", action={"kind": "click", "target": {"kind": "ref", "ref": submit}}
            )
            assert '"verification": "passed"' in await call(
                "browser_wait", condition={"kind": "text", "text": "clicked"}
            )
            assert (await backend.screenshot()).data.startswith(b"\xff\xd8")
        targets = await existing.send("Target.getTargets")
        identities = {info["targetId"] for info in targets["targetInfos"]}
        assert target not in identities and other.target in identities
        assert process.returncode is None
        assert await existing.evaluate(other, "document.title") == ""
    finally:
        server.close()
        await server.wait_closed()
        await existing.close()


@pytest.mark.parametrize(
    "markup,value_expression",
    [
        ('<input aria-label="Editor" value="old">', "document.querySelector('input').value"),
        ('<textarea aria-label="Editor">old</textarea>', "document.querySelector('textarea').value"),
        (
            '<div contenteditable="true" role="textbox" aria-label="Editor">old</div>',
            "document.querySelector('[contenteditable]').textContent",
        ),
    ],
)
async def test_fill_replaces_existing_text(native_browser, markup, value_expression):
    endpoint, _ = native_browser
    async with CdpBrowserBackend.connect(endpoint) as backend:
        await backend.navigate("data:text/html," + markup)
        for text in ("replacement", ""):
            observation = await backend.observe()
            element = next(item for item in observation.elements if item.description == "Editor")
            await backend.interact(
                Fill(kind="fill", target=Ref(kind="ref", ref=element.target), text=text), revision=observation.revision
            )
            assert await backend.browser.evaluate(backend.page, value_expression) == text


async def test_scopes_survive_runs_reconcile_popups_and_reattach(native_browser):
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.capabilities.browser import BrowserError
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    endpoint, process = native_browser
    runtime = await Chromium.connect(endpoint)
    replacement = None
    try:
        left, right = CdpBrowserBackend(runtime), CdpBrowserBackend(runtime)
        await asyncio.gather(left.tabs("new"), right.tabs("new"))
        left_target, right_target = left.page.target, right.page.target
        await asyncio.gather(
            runtime.navigate(left.page, "data:text/html,<title>Left</title><button>Left action</button>"),
            runtime.navigate(right.page, "data:text/html,<title>Right</title><button>Right action</button>"),
        )
        assert {tab.target for tab in await left.tabs("list")} == {left_target}
        with pytest.raises(BrowserError, match="not owned"):
            await left.tabs("select", target=right_target)

        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls % 2:
                yield {0: DeltaToolCall(name="browser_observe", json_args="{}", tool_call_id=str(calls))}
            else:
                assert "Left action" in str(messages)
                yield "observed"

        executable = HarnessBuilder().build(
            AgentSpec(),
            model=FunctionModel(stream_function=model),
            capabilities=(BrowserUseCapability(),),
            output_type=str,
        )
        first = await executable.run("observe", bindings=RunBindings.embedded(browser_backend=left))
        assert first.output_or_raise() == "observed"
        second = await executable.run(
            "observe again", previous_state=first.state, bindings=RunBindings.embedded(browser_backend=left)
        )
        assert second.output_or_raise() == "observed"
        assert left.page.target == left_target

        await runtime.send(
            "Runtime.evaluate", {"expression": "window.open('about:blank') !== null", "userGesture": True}, left.page
        )
        async with asyncio.timeout(5):
            while len(await left.tabs("list")) != 2:
                await asyncio.sleep(0.025)
        popup = next(target for target in left.targets if target != left_target)
        assert popup not in right.targets
        await runtime.send("Target.closeTarget", {"targetId": popup})
        async with asyncio.timeout(5):
            while popup in left.targets:
                await asyncio.sleep(0.025)
        await runtime.close()
        assert process.returncode is None
        replacement = await Chromium.connect(endpoint)
        restored = CdpBrowserBackend(replacement)
        await restored.attach(left_target)
        assert "Observe" in await restored.interact(Press(kind="press", key="Enter"))
        assert "Left action" in (await restored.observe()).text
        await restored.close()
        ids = {item["targetId"] for item in (await replacement.send("Target.getTargets"))["targetInfos"]}
        assert left_target not in ids and right_target in ids
        await replacement.send("Target.closeTarget", {"targetId": right_target})
    finally:
        if not runtime.disconnected:
            await runtime.close()
        if replacement is not None:
            await replacement.close()
