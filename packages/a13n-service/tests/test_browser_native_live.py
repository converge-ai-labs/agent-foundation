"""Opt-in Linux desktop validation using operator-installed Chromium and VNC binaries."""

import asyncio
import os
import struct
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_service.providers.browser.definition import BrowserProviderError
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Recipe, blocking
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .environment_providers.desktop_access_rfb import RFB
from .http_support import serve_http

pytestmark = pytest.mark.anyio


async def test_native_profile_input_and_recovery(tmp_path, monkeypatch):
    chromium = os.environ.get("A13N_TEST_BROWSER_NATIVE_CHROMIUM")
    if not chromium:
        pytest.skip("Requires A13N_TEST_BROWSER_NATIVE_CHROMIUM and installed Linux desktop binaries")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test-not-owned")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    identity = "browser_native_" + uuid4().hex[:16]
    provider = Native(tmp_path, chromium)
    connection = None
    rfb = None
    app = FastAPI()

    @app.get("/")
    async def page_fixture():
        return HTMLResponse("<label>Entry<input autofocus></label>")

    try:
        async with serve_http(app) as origin:
            live = await provider.create(identity, Recipe())
            assert live.status == "ready" and live.runtime_id
            connection = await provider.connect(live.state)
            target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
            page = await connection.attach(target)
            await connection.navigate(page, origin)
            await connection.send("Page.bringToFront", page=page)
            await connection.evaluate(
                page, "document.querySelector('input').focus();localStorage.setItem('retained','native')"
            )
            cookie = await connection.send(
                "Network.setCookie",
                {
                    "name": "retained",
                    "value": identity,
                    "url": origin,
                    "expires": (datetime.now(UTC) + timedelta(days=1)).timestamp(),
                },
                page,
            )
            assert cookie["success"]
            assert identity in await connection.evaluate(page, "document.cookie")
            grant = await provider.ensure_viewer(
                live.state, "native_input", expires_at=datetime.now(UTC) + timedelta(seconds=8)
            )
            rfb = await blocking(RFB, grant.url, grant.state["password"])
            assert (await blocking(rfb.frame))["pixel_bytes"] > 0
            for down in (1, 0):
                await blocking(rfb.send, struct.pack("!BBHI", 4, down, 0, ord("n")))
            async with asyncio.timeout(5):
                while await connection.evaluate(page, "document.querySelector('input').value") != "n":
                    await asyncio.sleep(0.1)
            await provider.close()
            await asyncio.sleep(max(0, (grant.expires_at - datetime.now(UTC)).total_seconds()) + 0.5)
            assert await blocking(rfb.closed)
            await blocking(rfb.close)
            rfb = None
            provider = Native(tmp_path, chromium)
            recovered = await provider.inspect(identity, live.state)
            assert recovered and recovered.runtime_id == live.runtime_id
            # Launch refuses even a direct second launch while this exact writer remains alive.
            async with provider.allocation(identity) as allocation:
                with pytest.raises(BrowserProviderError) as refused:
                    await provider.launch(allocation)
                assert refused.value.code == "native_writer_unconfirmed"
            await connection.close()
            connection = None
            stopped = await provider.stop(identity, live.state)
            assert stopped.status == "stopped"
            resumed = await provider.start(identity, stopped.state, generation=2)
            assert resumed.runtime_id != live.runtime_id
            connection = await provider.connect(resumed.state)
            target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
            page = await connection.attach(target)
            await connection.navigate(page, origin)
            assert identity in await connection.evaluate(page, "document.cookie")
            assert await connection.evaluate(page, "localStorage.getItem('retained')") == "native"
            with pytest.raises(BrowserProviderError):
                await provider.ensure_viewer(
                    live.state, "late_native_input", expires_at=datetime.now(UTC) + timedelta(seconds=60)
                )
            await provider.delete(identity, resumed.state)
            assert await provider.inspect(identity, resumed.state) is None
            async with provider.allocation(identity) as allocation:
                assert not (allocation.root / "data").exists()
            with pytest.raises(BrowserProviderError):
                await provider.start(identity, resumed.state, generation=3)
    except BaseException:
        for log in tmp_path.glob("*/runtime.log"):
            print(log.read_text()[-12000:])
        raise
    finally:
        if rfb is not None:
            await blocking(rfb.close)
        if connection is not None:
            await connection.close()
        await provider.delete(identity, None)
        await provider.close()


@pytest.mark.parametrize("shutdown", ["runtime_signal", "parent_exit"])
async def test_native_exit_flushes_fresh_profile(tmp_path, shutdown):
    """Runtime shutdown, including parent death, must not depend on Service CDP cleanup."""
    import json
    import signal
    import sys

    from a13n_service.providers.browser.runtime import group_alive

    chromium = os.environ.get("A13N_TEST_BROWSER_NATIVE_CHROMIUM")
    if not chromium:
        pytest.skip("Requires A13N_TEST_BROWSER_NATIVE_CHROMIUM and installed Linux desktop binaries")
    identity = "browser_parent_" + uuid4().hex[:16]
    provider = Native(tmp_path, chromium)
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        """
import asyncio, json, sys
from pathlib import Path
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Recipe
async def main():
    provider = Native(Path(sys.argv[1]), sys.argv[2])
    live = await provider.create(sys.argv[3], Recipe())
    async with provider.allocation(sys.argv[3]) as allocation:
        info = await provider.information(allocation)
    print(json.dumps({'state': live.state, 'pid': info['pid']}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
""",
        str(tmp_path),
        chromium,
        identity,
        stdout=asyncio.subprocess.PIPE,
    )
    connection = None
    app = FastAPI()

    @app.get("/")
    async def page_fixture():
        return HTMLResponse("Native parent-exit profile fixture")

    try:
        assert parent.stdout is not None
        saved = json.loads(await asyncio.wait_for(parent.stdout.readline(), 45))
        async with serve_http(app) as origin:
            connection = await provider.connect(saved["state"])
            old_target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
            page = await connection.attach(old_target)
            await connection.navigate(page, origin)
            await connection.send(
                "Network.setCookie",
                {
                    "name": "fresh",
                    "value": identity,
                    "url": origin,
                    "expires": (datetime.now(UTC) + timedelta(days=1)).timestamp(),
                },
                page,
            )
            assert identity in await connection.evaluate(page, "document.cookie")
            await connection.evaluate(page, "localStorage.setItem('fresh','retained')")
            # No flush delay and no provider.stop / Browser.close call from this client.
            if shutdown == "parent_exit":
                parent.terminate()
                await parent.wait()
            else:
                os.kill(saved["pid"], signal.SIGTERM)
            async with asyncio.timeout(20):
                while await blocking(group_alive, saved["pid"]):
                    await asyncio.sleep(0.05)
            await connection.close()
            connection = None
            stopped = await provider.inspect(identity, saved["state"])
            assert stopped and stopped.status == "stopped"
            live = await provider.start(identity, stopped.state, generation=2)
            assert live.runtime_id != saved["state"]["runtime_id"]
            connection = await provider.connect(live.state)
            targets = (await connection.send("Target.getTargets"))["targetInfos"]
            assert old_target not in {target["targetId"] for target in targets}
            target = (await connection.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
            page = await connection.attach(target)
            await connection.navigate(page, origin)
            assert identity in await connection.evaluate(page, "document.cookie")
            assert await connection.evaluate(page, "localStorage.getItem('fresh')") == "retained"
    finally:
        if connection is not None:
            await connection.close()
        if parent.returncode is None:
            parent.terminate()
            await parent.wait()
        await provider.delete(identity, None)
        await provider.close()
