"""Opt-in CDP integration without Harness UI, models or a display server."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from a13n_harness.capabilities import BrowserUseCapability
from a13n_harness.capabilities.browser.cdp import CdpBrowserBackend, Chromium

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_BROWSER_REAL_TEST") != "1", reason="Opt-in native Chromium test"),
]
HTML = b"""<!doctype html><title>Harness CDP fixture</title><input aria-label="Message"><button onclick="document.querySelector('output').textContent='clicked'">Submit</button><output></output>"""


@pytest.fixture
async def native_browser(request):
    with TemporaryDirectory(prefix="harness-cdp-") as directory:
        arguments = [
            os.environ["A13N_BROWSER_EXECUTABLE"],
            f"--user-data-dir={directory}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--headless=new",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if os.name == "posix" and os.geteuid() == 0:
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
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            endpoint = Path(directory) / "DevToolsActivePort"
            async with asyncio.timeout(20):
                while True:
                    if process.returncode is not None:
                        pytest.fail("Chromium exited before CDP startup")
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

            async def call(name, **kwargs):
                return await tools[name].function_schema.call(kwargs, None)

            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            assert "completed" in await call("browser_navigate", url=url)
            assert "Harness CDP fixture" in await call("browser_observe")
            observation = await backend.observe()
            field = next(
                index for index, element in enumerate(observation.elements, 1) if element.description == "Message"
            )
            # Refresh the model-facing compact references after the direct observation.
            await call("browser_observe")
            assert "completed" in await call("browser_fill", ref=f"e{field}", text="Harness only")
            assert (
                await backend.browser.evaluate(backend.page, "document.querySelector('input').value") == "Harness only"
            )
            observation = await call("browser_observe")
            submit = next(line.split("]")[0][1:] for line in observation.splitlines() if line.endswith("] Submit"))
            assert "completed" in await call("browser_click", ref=submit)
            assert "completed" in await call("browser_wait", text="clicked")
            assert (await backend.screenshot()).startswith(b"\xff\xd8")
        targets = await existing.send("Target.getTargets")
        identities = {info["targetId"] for info in targets["targetInfos"]}
        assert target not in identities and other.target in identities
        assert process.returncode is None
        assert await existing.evaluate(other, "document.title") == ""
    finally:
        server.close()
        await server.wait_closed()
        await existing.close()
