"""Listener exit is observable and does not wait for infinite browser streams."""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import httpx
import psutil
import pytest
import uvicorn
from a13n_harness_ui.webui import create_webui, run
from a13n_harness_ui.webui_lifecycle import RequestLog, WebUIServer
from anyio import Event, fail_after, sleep, sleep_forever
from starlette.routing import Route
from starlette.websockets import WebSocket
from websockets.sync.client import connect

from .test_app import _write_configuration


@pytest.mark.anyio
async def test_realtime_shutdown_serializes_close_with_pending_channel_send() -> None:
    stopping, close_started, channel_started = Event(), Event(), Event()
    stopping.set()
    messages = []
    received = 0

    @asynccontextmanager
    async def unopened_app():
        raise AssertionError("This transport race does not open an App")
        yield

    async def receive():
        nonlocal received
        received += 1
        if received == 1:
            return {"type": "websocket.connect"}
        if received == 2:
            return {"type": "websocket.receive", "text": "{}"}
        if received == 3:
            await close_started.wait()
            return {
                "type": "websocket.receive",
                "text": json.dumps({"version": 1, "kind": "subscribe", "channel": "summary", "stream": "summary"}),
            }
        channel_started.set()
        await sleep_forever()

    async def send(message):
        messages.append(message)
        if message["type"] == "websocket.close":
            # Starlette has entered DISCONNECTED, but the ASGI close is still
            # pending. Force an observer to send in precisely this interval.
            close_started.set()
            await channel_started.wait()
            await sleep(0)

    server = create_webui(unopened_app, api_key=None, stopping=stopping)
    endpoint = next(route.endpoint for route in server.routes if route.path == "/api/realtime/connect")
    with fail_after(2):
        await endpoint(WebSocket({"type": "websocket"}, receive, send))
    assert [message["type"] for message in messages] == ["websocket.accept", "websocket.close"]
    assert messages[-1]["code"] == 1001


@pytest.mark.anyio
async def test_request_logs_use_route_template_not_private_inputs(caplog: pytest.LogCaptureFixture) -> None:
    async def endpoint(request):
        pass

    async def app(scope, receive, send):
        scope["route"] = Route("/api/threads/{thread_id}", endpoint)
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"private response"})

    async def receive():
        return {"type": "http.request", "body": b"private request"}

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger="a13n_harness_ui.webui"):
        await RequestLog(app)(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/threads/private-thread",
                "query_string": b"api_key=private-secret&path=/private/folder",
            },
            receive,
            send,
        )
    assert "GET /api/threads/{thread_id} → 404" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.anyio
async def test_slow_cleanup_remains_observable_without_abandoning_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stopping = Event()
    cleaned = False

    async def cleanup(self, sockets=None):
        nonlocal cleaned
        assert stopping.is_set()
        await sleep(2.1)
        cleaned = True

    monkeypatch.setattr(uvicorn.Server, "shutdown", cleanup)
    server = WebUIServer(uvicorn.Config("unused:app", log_config=None), stopping=stopping)
    with caplog.at_level(logging.INFO, logger="a13n_harness_ui.webui"):
        await server.shutdown()
    assert cleaned
    assert "Still stopping WebUI" in caplog.text
    assert "WebUI stopped." in caplog.text


@pytest.mark.anyio
async def test_failed_startup_is_not_reported_as_success(caplog: pytest.LogCaptureFixture) -> None:
    @asynccontextmanager
    async def broken_app():
        raise RuntimeError("Fixture startup failure")
        yield  # pragma: no cover

    with caplog.at_level(logging.INFO, logger="a13n_harness_ui.webui"):
        with pytest.raises(SystemExit) as failure:
            await run(broken_app, port=0, api_key="test-key")
    assert failure.value.code == 3
    assert "WebUI ready" not in caplog.text


def test_stop_request_is_visible_even_during_startup(caplog: pytest.LogCaptureFixture) -> None:
    server = WebUIServer(uvicorn.Config("unused:app", log_config=None), stopping=Event())
    with caplog.at_level(logging.INFO, logger="a13n_harness_ui.webui"):
        server.handle_exit(signal.SIGINT, None)
    assert server.should_exit
    assert "Stop requested. Waiting for WebUI startup or cleanup" in caplog.text


_SERVER = """
import asyncio
import sys
from pathlib import Path
from a13n_logging import configure_logging, LogFormat
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.webui import run
from pydantic_ai.models.function import FunctionModel

root = Path(sys.argv[1])
async def stream(messages, info):
    try:
        yield 'Working'
        (root / 'run-started').touch()
        await asyncio.sleep(3600)
    finally:
        (root / 'run-stopped').touch()
async def resolve(self, context, model_id):
    return FunctionModel(stream_function=stream)
HarnessUiModelResolver.__call__ = resolve
configure_logging(logger_names=('a13n_harness_ui', 'uvicorn'), log_format=LogFormat.json)
settings = HarnessUiSettings(storage=StorageSettings(data_root=root / 'data'), pricing_auto_update=False, shutdown_timeout_seconds=1)
try:
    asyncio.run(run(
        lambda: open_harness_ui_app(settings, configuration_path=root / 'a13n-harness-ui.yaml', host_mode='webui', share_computer=True, instrumentation=None),
        port=int(sys.argv[2]), api_key='lifecycle-test-key',
    ))
except KeyboardInterrupt:
    pass
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals and native PTY")
@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_closes_live_browser_streams_and_owned_run_and_pty(tmp_path: Path, stop_signal: signal.Signals) -> None:
    _write_configuration(tmp_path)
    # No personal shell startup files, provider traffic or browser dependency.
    env = {**os.environ, "HOME": str(tmp_path), "SHELL": "/bin/sh", "ENV": "/dev/null", "BASH_ENV": "/dev/null"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _SERVER, str(tmp_path), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    terminal_pid = None
    output = ""
    try:
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer lifecycle-test-key"},
                trust_env=False,
                timeout=5,
            ) as api,
            ExitStack() as clients,
        ):
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, process.communicate()[0]
                try:
                    if api.get("/readyz").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < deadline, "Listener never became ready"
                time.sleep(0.02)
            created = api.post("/api/threads", json={})
            assert created.status_code == 200, created.text
            thread = created.json()["thread_id"]
            submitted = api.post(f"/api/threads/{thread}/submit", json={"prompt": "Keep working until shutdown"})
            assert submitted.status_code == 200, submitted.text
            terminal = api.post("/api/host/terminals", json={"cwd": str(tmp_path)}).json()
            assert "terminal_id" in terminal, terminal
            realtime = clients.enter_context(connect(f"ws://127.0.0.1:{port}/api/realtime/connect", proxy=None))
            realtime.send(json.dumps({"api_key": "lifecycle-test-key"}))
            for stream in ("summary", "focus"):
                realtime.send(
                    json.dumps(
                        {
                            "version": 1,
                            "kind": "subscribe",
                            "channel": stream,
                            "stream": stream,
                            "root_thread_id": thread if stream == "focus" else None,
                        }
                    )
                )
                while json.loads(realtime.recv(timeout=5)).get("channel") != stream:
                    pass
            ws = clients.enter_context(
                connect(f"ws://127.0.0.1:{port}/api/host/terminals/{terminal['terminal_id']}/connect", proxy=None)
            )
            ws.send(json.dumps({"api_key": "lifecycle-test-key"}))
            initial = json.loads(ws.recv())
            assert initial["kind"] == "terminal"
            ws.send(json.dumps({"kind": "control", "control_epoch": 0}))
            while True:
                controlled = json.loads(ws.recv(timeout=5))
                if controlled.get("terminal", {}).get("controller") == initial["participant_id"]:
                    break
            ws.send(
                json.dumps(
                    {
                        "kind": "input",
                        "control_epoch": controlled["terminal"]["control_epoch"],
                        "text": f"echo $$ > {shlex.quote(str(tmp_path / 'shell-pid'))}\n",
                    }
                )
            )
            deadline = time.monotonic() + 5
            while not (tmp_path / "shell-pid").exists():
                assert time.monotonic() < deadline, "PTY did not accept input"
                time.sleep(0.02)
            terminal_pid = int((tmp_path / "shell-pid").read_text())
            assert psutil.pid_exists(terminal_pid)
            deadline = time.monotonic() + 5
            while not (tmp_path / "run-started").exists():
                assert time.monotonic() < deadline, "Run did not start"
                time.sleep(0.02)
            started = time.monotonic()
            process.send_signal(stop_signal)
            output, _ = process.communicate(timeout=8)
            elapsed = time.monotonic() - started
            assert elapsed < 3, output
        assert (tmp_path / "run-stopped").exists(), output
        assert not psutil.pid_exists(terminal_pid), output
        for message in (
            "Starting WebUI",
            "WebUI ready",
            "Stop requested.",
            "Stopping WebUI",
            "WebUI stopped",
        ):
            assert message in output
        assert "POST /api/threads/{thread_id}/submit" in output
        assert "timeout graceful shutdown exceeded" not in output
        assert "Traceback" not in output
        assert "lifecycle-test-key" not in output
        assert str(tmp_path) not in output
        assert process.returncode in (0, -stop_signal), output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if terminal_pid and psutil.pid_exists(terminal_pid):
            os.killpg(terminal_pid, signal.SIGKILL)


@pytest.mark.anyio
async def test_projection_errors_log_safe_reasons_and_thread_identity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.webui import create_webui

    _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    server = create_webui(
        lambda: open_harness_ui_app(settings, configuration_path=tmp_path / "a13n-harness-ui.yaml"),
        api_key="private-test-key",
    )
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer private-test-key"},
        ) as client:
            thread = (await client.post("/api/threads", json={})).json()
            thread_id = thread["thread_id"]
            for endpoint, status, code, reason in (
                ("transcript", 400, "thread_history_continuation_changed", "Saved history changed"),
                ("tasks", 409, "thread_continuation_conflict", "selected continuation changed"),
            ):
                caplog.clear()
                with caplog.at_level(logging.WARNING, logger="a13n_harness_ui.webui"):
                    response = await client.get(
                        f"/api/threads/{thread_id}/{endpoint}",
                        params={"expected_continuation_id": "private-stale-continuation"},
                    )
                assert response.status_code == status
                assert response.json()["error"]["code"] == code
                records = [record for record in caplog.records if record.name == "a13n_harness_ui.webui"]
                assert len(records) == 1
                assert records[0].error_code == code
                assert records[0].thread_id == thread_id
                assert reason in records[0].reason
                assert "error_code=" not in records[0].getMessage()
                assert "private" not in caplog.text


@pytest.mark.anyio
async def test_error_diagnostics_do_not_log_dynamic_messages_or_arbitrary_route_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from a13n_harness_ui.webui import _error

    async def endpoint(request):
        pass

    async def app(scope, receive, send):
        scope["route"] = Route("/api/threads/{thread_id}/transcript", endpoint)
        scope["path_params"] = {"thread_id": "/private/folder", "path": "/private/config"}
        await _error("configuration_invalid", "private-secret and /private/folder", 400)(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"private request"}

    messages = []

    async def send(message):
        messages.append(message)

    with caplog.at_level(logging.WARNING, logger="a13n_harness_ui.webui"):
        await RequestLog(app)({"type": "http", "method": "GET", "path": "/private/folder"}, receive, send)
    assert caplog.records[-1].error_code == "configuration_invalid"
    assert "private" not in caplog.text
    # The response body remains available to its authenticated caller, unmodified.
    assert b"private-secret" in messages[-1]["body"]


@pytest.mark.parametrize("separator", ["_", "-"])
def test_ordered_prompt_preserves_presentation_source_identity(separator: str) -> None:
    from a13n_harness_ui.app import ComposerInput
    from a13n_harness_ui.webui import PromptRequest

    source_id = f"input{separator}{'a' * 32}"
    request = PromptRequest(parts=("hello",), source_id=source_id)
    captured = request.input()
    assert isinstance(captured, ComposerInput)
    assert captured.source_id == source_id
    assert PromptRequest(prompt="legacy").input() == "legacy"


@pytest.mark.parametrize(
    "values",
    [
        {"prompt": "legacy", "source_id": "input_" + "a" * 32},
        {"parts": ["hello"], "source_id": "not-an-input-id"},
        {"parts": ["hello"], "source_id": "input_" + "A" * 32},
    ],
)
def test_prompt_rejects_invalid_presentation_identity(values: dict[str, object]) -> None:
    from a13n_harness_ui.webui import PromptRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PromptRequest.model_validate(values)
