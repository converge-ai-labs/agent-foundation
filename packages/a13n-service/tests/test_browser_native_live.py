"""Opt-in Linux desktop validation using operator-installed Chromium and VNC binaries."""

import asyncio
import base64
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


def native_display(root):
    for proc in Path("/proc").glob("[0-9]*/environ"):
        try:
            env = dict(item.split(b"=", 1) for item in proc.read_bytes().split(b"\0") if b"=" in item)
            if str(root).encode() in env.get(b"XAUTHORITY", b"") and b"DISPLAY" in env:
                return {**os.environ, "DISPLAY": env[b"DISPLAY"].decode(), "XAUTHORITY": env[b"XAUTHORITY"].decode()}
        except (OSError, ValueError):
            continue
    raise RuntimeError("test display not found")


def native_windows(root):
    env = native_display(root)
    tree = subprocess.run(["xwininfo", "-root", "-tree"], env=env, capture_output=True, text=True, timeout=2)
    # Output only window IDs and geometry, not titles or page URLs.
    windows = re.findall(r"(0x[0-9a-f]+) .*? (\d+x\d+[+-]\d+[+-]\d+)", tree.stdout)[:50]
    focused = native_x11_focus(root).get("input_focus", "")
    if re.fullmatch(r"0x[0-9a-f]+", focused) and focused not in {xid for xid, _ in windows}:
        windows.append((focused, "unknown"))
    details = []
    window_pids = set()
    for xid, geometry in windows:
        props = subprocess.run(
            ["xprop", "-id", xid, "WM_CLASS", "_NET_WM_PID", "WM_STATE", "_NET_WM_STATE"],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
        )
        lines = [
            line[:180]
            for line in props.stdout.splitlines()
            if line.startswith(("WM_CLASS(", "_NET_WM_PID(", "WM_STATE(", "_NET_WM_STATE(", "\t\twindow state:"))
        ]
        for line in lines:
            match = re.fullmatch(r"_NET_WM_PID\(CARDINAL\) = (\d+)", line)
            if match:
                window_pids.add(int(match.group(1)))
        attributes = subprocess.run(["xwininfo", "-id", xid], env=env, capture_output=True, text=True, timeout=2)
        state = next((line.strip() for line in attributes.stdout.splitlines() if "Map State:" in line), "unknown")
        details.append({"xid": xid, "geometry": geometry, "map_state": state, "properties": lines})
    processes = []
    for proc in Path("/proc").glob("[0-9]*/environ"):
        try:
            fields = dict(item.split(b"=", 1) for item in proc.read_bytes().split(b"\0") if b"=" in item)
            pid = int(proc.parent.name)
            owned_display = fields.get(b"XAUTHORITY", b"").decode() == env["XAUTHORITY"]
            if not owned_display and pid not in window_pids:
                continue
            exe = os.readlink(proc.parent / "exe")
            if pid in window_pids or Path(exe).name in ("chrome", "chromium", "x11vnc", "openbox"):
                processes.append(
                    {"pid": int(proc.parent.name), "exe": exe, "display": fields.get(b"DISPLAY", b"").decode()}
                )
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return {"display": env["DISPLAY"], "windows": details, "processes": processes[:30]}


async def start_raw_probe(root):
    try:
        env = await blocking(native_display, root)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("native_input_probe.py")),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdout is not None
        try:
            ready = await asyncio.wait_for(process.stdout.readline(), 2)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if ready != b"ready\n":
            if process.returncode is None:
                process.kill()
            await process.wait()
            return None, "probe failed to start"
        return process, "ready"
    except Exception as exc:
        return None, f"probe unavailable: {type(exc).__name__}"


async def stop_raw_probe(process):
    if process is None:
        return []
    already_exited = process.returncode is not None
    if not already_exited:
        process.terminate()
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 2)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    if already_exited:
        raise RuntimeError(f"probe exited early: {process.returncode}")
    return [line for line in output.decode(errors="replace").splitlines() if line in ("raw_press", "raw_release")][:20]


def native_x11_focus(root, screenshot=None):
    """Inspect only the display belonging to this test's allocation."""
    for proc in Path("/proc").glob("[0-9]*/environ"):
        try:
            env = dict(item.split(b"=", 1) for item in proc.read_bytes().split(b"\0") if b"=" in item)
            if str(root).encode() not in env.get(b"XAUTHORITY", b"") or b"DISPLAY" not in env:
                continue
            display_env = {**os.environ, "DISPLAY": env[b"DISPLAY"].decode(), "XAUTHORITY": env[b"XAUTHORITY"].decode()}
            code = (
                "import ctypes; x=ctypes.CDLL('libX11.so.6'); x.XOpenDisplay.restype=ctypes.c_void_p; "
                "d=x.XOpenDisplay(None); assert d; w=ctypes.c_ulong(); r=ctypes.c_int(); "
                "x.XGetInputFocus(ctypes.c_void_p(d),ctypes.byref(w),ctypes.byref(r)); print(hex(w.value))"
            )
            focus = subprocess.run(["python", "-c", code], env=display_env, capture_output=True, text=True, timeout=2)
            active = subprocess.run(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"], env=display_env, capture_output=True, text=True, timeout=2
            )
            result = {"input_focus": focus.stdout.strip(), "active_window": active.stdout.strip()}
            if screenshot is not None:
                try:
                    capture = subprocess.run(
                        ["xwd", "-root", "-silent", "-out", str(screenshot)],
                        env=display_env,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    result["desktop_capture"] = f"exit {capture.returncode}: {capture.stderr.strip()}"
                except (OSError, subprocess.TimeoutExpired) as exc:
                    result["desktop_capture"] = str(exc)
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return {"error": "test display not found"}


async def test_native_profile_input_and_recovery(tmp_path, monkeypatch):
    chromium = os.environ.get("A13N_TEST_BROWSER_NATIVE_CHROMIUM")
    if not chromium:
        pytest.skip("Requires A13N_TEST_BROWSER_NATIVE_CHROMIUM and installed Linux desktop binaries")
    x11vnc = shutil.which("x11vnc")
    assert x11vnc
    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    debug_log = tmp_path / "x11vnc-debug.log"
    wrapper = wrapper_dir / "x11vnc"
    wrapper.write_text(
        f'#!/bin/sh\nif [ "$1" = -storepasswd ]; then exec {shlex.quote(x11vnc)} "$@"; fi\n'
        f'exec {shlex.quote(x11vnc)} -dk -o {shlex.quote(str(debug_log))} "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper_dir}:{os.environ['PATH']}")
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
            frame = await blocking(rfb.frame)
            assert frame["pixel_bytes"] > 0

            async def input_diagnostics(screenshot=None):
                return {
                    "grant_seconds_remaining": (grant.expires_at - datetime.now(UTC)).total_seconds(),
                    "dom": await connection.evaluate(
                        page, "({hasFocus: document.hasFocus(), active: document.activeElement.outerHTML})"
                    ),
                    "x11": await blocking(native_x11_focus, tmp_path, screenshot),
                }

            await connection.evaluate(
                page,
                "window.keyEvents = []; ['keydown', 'keypress', 'keyup', 'input'].forEach(t => "
                "document.addEventListener(t, e => window.keyEvents.push([t, e.key, e.isTrusted]), true))",
            )
            try:
                before_input = await input_diagnostics()
            except Exception as exc:
                before_input = {"error": type(exc).__name__}
            probe, probe_status = await start_raw_probe(tmp_path)
            before_input["send_seconds_remaining"] = (grant.expires_at - datetime.now(UTC)).total_seconds()
            raw_events = []
            try:
                try:
                    for down in (1, 0):
                        await blocking(rfb.send, struct.pack("!BBHI", 4, down, 0, ord("n")))
                    async with asyncio.timeout(5):
                        while await connection.evaluate(page, "document.querySelector('input').value") != "n":
                            await asyncio.sleep(0.1)
                finally:
                    try:
                        raw_events = await stop_raw_probe(probe)
                    except Exception as exc:
                        probe_status = f"probe cleanup error: {type(exc).__name__}"
            except TimeoutError:
                try:
                    try:
                        window_info = await blocking(native_windows, tmp_path)
                        cdp_window = await connection.send("Browser.getWindowForTarget", {"targetId": target})
                    except Exception as exc:
                        window_info, cdp_window = f"diagnostic error: {type(exc).__name__}", None
                    output = Path("test-results")
                    output.mkdir(exist_ok=True)
                    print(
                        "Native input diagnostics:",
                        {
                            "frame": frame,
                            "window_info": window_info,
                            "cdp_window": cdp_window,
                            "raw_probe": {"status": probe_status, "events": raw_events},
                            "before": before_input,
                            "after": await input_diagnostics(output / "native-input-desktop.xwd"),
                            "dom_events": await connection.evaluate(page, "window.keyEvents"),
                            "x11vnc_events": [
                                line
                                for line in debug_log.read_text().splitlines()
                                if "# keyboard(" in line or "XTestFakeKeyEvent(" in line
                            ]
                            if debug_log.exists()
                            else "debug log missing",
                        },
                    )
                    screenshot = await connection.send("Page.captureScreenshot", page=page)
                    (output / "native-input-page.png").write_bytes(base64.b64decode(screenshot["data"]))
                except Exception as exc:
                    print(f"Native input diagnostics failed: {type(exc).__name__}")
                raise
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


async def test_native_survives_executor_thread_exit(tmp_path, monkeypatch):
    """The runtime is owned by the Service process, not a transient I/O worker."""
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial

    from a13n_service.providers.browser import native

    chromium = os.environ.get("A13N_TEST_BROWSER_NATIVE_CHROMIUM")
    if not chromium:
        pytest.skip("Requires A13N_TEST_BROWSER_NATIVE_CHROMIUM and installed Linux desktop binaries")
    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(lambda: None).result()
    original_blocking = native.blocking

    async def transient_spawn(function, *args, **kwargs):
        if function is subprocess.Popen:
            return await asyncio.get_running_loop().run_in_executor(executor, partial(function, *args, **kwargs))
        return await original_blocking(function, *args, **kwargs)

    monkeypatch.setattr(native, "blocking", transient_spawn)
    identity = "browser_worker_" + uuid4().hex[:16]
    provider = Native(tmp_path, chromium)
    try:
        live = await provider.create(identity, Recipe())
        executor.shutdown(wait=True)
        await asyncio.sleep(1)
        observed = await provider.inspect(identity, live.state)
        assert observed and observed.status == "ready" and observed.runtime_id == live.runtime_id
    finally:
        executor.shutdown(wait=True)
        await provider.delete(identity, None)
        await provider.close()
