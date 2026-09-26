"""Real loopback protocol clients exercise the App, not a browser mock."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from a13n_harness_ui.app import HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui
from anyio import fail_after, sleep
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def listener(
    tmp_path: Path, *, sharing: bool = True, configuration_path: Path | None = None
) -> AsyncIterator[tuple[str, str]]:
    async with listener_with_app(tmp_path, sharing=sharing, configuration_path=configuration_path) as (http, ws, _):
        yield http, ws


@asynccontextmanager
async def listener_with_app(
    tmp_path: Path, *, sharing: bool = True, configuration_path: Path | None = None
) -> AsyncIterator[tuple[str, str, HarnessUiApp]]:
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    app: HarnessUiApp | None = None

    @asynccontextmanager
    async def factory() -> AsyncIterator[HarnessUiApp]:
        nonlocal app
        async with open_harness_ui_app(
            settings,
            configuration_path=configuration_path,
            host_mode="webui",
            share_computer=sharing,
            instrumentation=None,
        ) as opened:
            app = opened
            yield opened

    server = create_webui(factory, api_key="test-only-key")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    native = uvicorn.Server(uvicorn.Config(server, log_level="error", lifespan="on", ws="websockets-sansio"))
    task = asyncio.create_task(native.serve(sockets=[sock]))
    try:
        with fail_after(10):
            while not native.started:
                if task.done():
                    await task
                    raise AssertionError("Listener stopped before startup")
                await sleep(0.01)
        assert app is not None
        yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}", app
    finally:
        native.should_exit = True
        with fail_after(10):
            await task
        sock.close()


async def frame_until(client: ClientConnection, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    with fail_after(5):
        while True:
            frame = json.loads(await client.recv())
            if predicate(frame):
                return frame


async def auth(client: ClientConnection) -> dict[str, Any]:
    await client.send(json.dumps({"api_key": "test-only-key"}))
    return await frame_until(client, lambda frame: frame.get("kind") == "terminal")


async def test_interactive_auth_precedes_resource_lookup_and_checks_origin(tmp_path: Path) -> None:
    async with listener(tmp_path) as (http, ws):
        path = ws + "/api/host/terminals/missing/connect"
        async with connect(path, proxy=None, origin=http) as client:
            await client.send('{"api_key":"wrong"}')
            with pytest.raises(ConnectionClosed) as failure:
                await client.recv()
            assert failure.value.rcvd.code == 4401
        async with connect(path, proxy=None, origin=http) as client:
            await client.send('{"api_key":"test-only-key"}')
            error = json.loads(await client.recv())
            expected = "host_terminal_not_found" if os.name == "posix" else "host_terminal_unavailable"
            assert error["error"]["code"] == expected
        with pytest.raises(InvalidStatus) as failure:
            async with connect(path, proxy=None, origin="http://attacker.invalid"):
                pass
        assert failure.value.response.status_code == 403
        async with httpx.AsyncClient(base_url=http, trust_env=False) as client:
            assert (await client.get("/api/host/terminals")).status_code == 401


@pytest.mark.skipif(os.name != "posix", reason="Native POSIX PTY")
async def test_two_clients_terminal_git_refresh_disconnect_and_app_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    async with listener(tmp_path) as (http, ws):
        async with httpx.AsyncClient(
            base_url=http, headers={"Authorization": "Bearer test-only-key"}, trust_env=False
        ) as client:
            created = await client.post("/api/host/terminals", json={"cwd": str(tmp_path)})
            assert created.status_code == 200, created.text
            terminal = created.json()
            identity = terminal["terminal_id"]
            path = ws + f"/api/host/terminals/{identity}/connect"
            async with (
                connect(path, proxy=None, origin=http) as first,
                connect(path, proxy=None, origin=http) as second,
            ):
                first_frame, second_frame = await auth(first), await auth(second)
                assert first_frame["participant_id"] != second_frame["participant_id"]
                await first.send(json.dumps({"kind": "control", "control_epoch": 0}))
                owned = await frame_until(first, lambda f: f.get("terminal", {}).get("control_epoch") == 1)
                assert owned["terminal"]["controller"] == first_frame["participant_id"]
                await second.send(json.dumps({"kind": "control", "control_epoch": 1}))
                owned = await frame_until(second, lambda f: f.get("terminal", {}).get("control_epoch") == 2)
                assert owned["terminal"]["controller"] == second_frame["participant_id"]
                await first.send(json.dumps({"kind": "input", "control_epoch": 1, "text": "touch forbidden\n"}))
                error = await frame_until(first, lambda f: "error" in f)
                assert error["error"]["code"] == "host_terminal_control_conflict"
                command = "stty -echo; git init -q; printf 'captured\\n' > sample; git add sample; printf '__%s__\\n' STAGED\n"
                await second.send(json.dumps({"kind": "input", "control_epoch": 2, "text": command}))
                await frame_until(second, lambda f: b"__STAGED__" in base64.b64decode(f.get("data_base64", "")))
                status = await client.get("/api/host/git/status", params={"path": str(tmp_path)})
                assert status.status_code == 200, status.text
                assert any(
                    item["path"] == "sample" and item["index_status"] == "A" for item in status.json()["entries"]
                )
                assert not (tmp_path / "forbidden").exists()
            async with connect(path, proxy=None, origin=http) as rejoined:
                observed = await auth(rejoined)
                assert observed["terminal"]["state"] == "running"
                assert b"__STAGED__" in base64.b64decode(observed["data_base64"])
                assert Path(observed["terminal"]["cwd"]).samefile(tmp_path)
            assert len((await client.get("/api/host/terminals")).json()) == 1
    # The listener's App owns PTY cleanup even without explicit HTTP close.
    async with listener(tmp_path) as (http, _):
        async with httpx.AsyncClient(
            base_url=http, headers={"Authorization": "Bearer test-only-key"}, trust_env=False
        ) as client:
            assert (await client.get("/api/host/terminals")).json() == []
            assert (await client.get(f"/api/host/terminals/{identity}")).status_code == 404


async def test_interactive_malformed_auth_and_sharing_opt_out(tmp_path: Path) -> None:
    async with listener(tmp_path, sharing=False) as (http, ws):
        path = ws + "/api/host/terminals/missing/connect"
        for malformed in (b"binary", "not-json", '{"api_key":"test-only-key","extra":1}'):
            async with connect(path, proxy=None, origin=http) as client:
                await client.send(malformed)
                with pytest.raises(ConnectionClosed) as failure:
                    await client.recv()
                assert failure.value.rcvd.code == 4401
        async with connect(path, proxy=None, origin=http) as client:
            await client.send('{"api_key":"test-only-key"}')
            error = json.loads(await client.recv())
            assert error["error"]["code"] == "host_terminal_disabled"
        async with httpx.AsyncClient(
            base_url=http, headers={"Authorization": "Bearer test-only-key"}, trust_env=False
        ) as client:
            status = (await client.get("/api/status")).json()
            assert not status["features"]["host_terminal"]
            assert (await client.post("/api/host/terminals", json={"cwd": str(tmp_path)})).status_code == 403
            contract = (await client.get("/api/openapi.json")).json()
            assert contract["x-interactive"]["terminal"]["input"]["$ref"].endswith("TerminalCommand")
            assert "TerminalFrame" in contract["components"]["schemas"]


@pytest.mark.skipif(os.name != "posix", reason="Native POSIX PTY")
async def test_blocked_input_disconnect_releases_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    async with listener(tmp_path) as (http, ws):
        async with httpx.AsyncClient(
            base_url=http, headers={"Authorization": "Bearer test-only-key"}, trust_env=False
        ) as client:
            identity = (await client.post("/api/host/terminals", json={"cwd": str(tmp_path)})).json()["terminal_id"]
            async with connect(ws + f"/api/host/terminals/{identity}/connect", proxy=None, origin=http) as connection:
                await auth(connection)
                await connection.send('{"kind":"control","control_epoch":0}')
                await frame_until(connection, lambda frame: frame.get("terminal", {}).get("control_epoch") == 1)
                await connection.send(
                    json.dumps(
                        {"kind": "input", "control_epoch": 1, "text": "stty raw -echo; printf '__%s__' RAW; sleep 30\n"}
                    )
                )
                await frame_until(
                    connection, lambda frame: b"__RAW__" in base64.b64decode(frame.get("data_base64", ""))
                )
                for _ in range(8):
                    await connection.send(json.dumps({"kind": "input", "control_epoch": 1, "text": "x" * 16384}))
            with fail_after(4):
                while True:
                    view = (await client.get(f"/api/host/terminals/{identity}")).json()
                    if not view["participants"]:
                        break
                    await sleep(0.02)
            assert view["controller"] is None and view["state"] == "running"
            closed = await client.delete(f"/api/host/terminals/{identity}")
            assert closed.status_code == 200 and closed.json()["state"] == "closed"
