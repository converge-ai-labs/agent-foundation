"""Real SIGTERM/SIGKILL -> same-data startup, with local deterministic tools/models."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest

from .test_app import _write_configuration

_SERVER = r"""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from a13n_harness_ui import model_catalog
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import run
from a13n_harness.codeact.state import CodeActStateOwner
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

root = Path(sys.argv[1])
mode = sys.argv[3]
owner = None
original_store = CodeActStateOwner.store
async def store(self, key, value, config):
    with (root / 'effects.jsonl').open('a') as log:
        log.write(json.dumps({'key': key}) + '\n')
    if key == 'uncertain-effect':
        (root / 'tool-started').touch()
        await asyncio.Event().wait()
    await original_store(self, key, value, config)
CodeActStateOwner.store = store
async def stream(messages, info):
    if any(isinstance(p, UserPromptPart) and 'Start an uncertain tool' in str(p.content) for m in messages for p in m.parts):
        yield {0: DeltaToolCall(name='store', tool_call_id='uncertain-once', json_args='{"key":"uncertain-effect","value":1}')}
        return
    returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
    with (root / 'requests.jsonl').open('a') as log:
        log.write(json.dumps({'returns': len(returned)}) + '\n')
    if not returned:
        (root / 'root-started').touch()
        if mode == 'graceful':
            await owner._restart.pause_requested.wait()
        yield {0: DeltaToolCall(name='store', tool_call_id='effect-once', json_args='{"key":"saved-effect","value":1}')}
    else:
        assert len([p for p in returned if p.tool_call_id == 'effect-once']) == 1
        if mode == 'safe' and not (root / 'allow-completion').exists():
            with (root / 'safe-requests.jsonl').open('a') as log:
                log.write('model-started\n')
            await asyncio.Event().wait()
        yield 'Continued after restart'
        (root / 'root-completed').touch()
async def resolve(self, context, model_id):
    return FunctionModel(stream_function=stream)
async def bundled():
    return model_catalog.bundled_models()
model_catalog.fetch_directory = bundled
HarnessUiModelResolver.__call__ = resolve
settings = HarnessUiSettings(storage=StorageSettings(data_root=root / 'data'), pricing_auto_update=False, shutdown_timeout_seconds=2)
@asynccontextmanager
async def app():
    global owner
    async with open_harness_ui_app(settings, configuration_path=root / 'a13n-harness-ui.yaml', host_mode='webui', instrumentation=None) as opened:
        owner = opened
        yield opened
try:
    asyncio.run(run(app, port=int(sys.argv[2]), api_key='restart-test-key'))
except KeyboardInterrupt:
    pass
"""


@contextmanager
def listener(root, *, mode="graceful", shutdown_signal=signal.SIGTERM):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_path = root / f"server-{port}.log"
    output_file = log_path.open("w")
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _SERVER, str(root), str(port), mode],
        stdout=output_file,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "HOME": str(root), "USERPROFILE": str(root)},
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer restart-test-key"},
            trust_env=False,
            timeout=5,
        ) as api:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, log_path.read_text()
                try:
                    if api.get("/readyz").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < deadline, f"Listener never became ready:\n{log_path.read_text()}"
                time.sleep(0.02)
            yield api
        process.send_signal(shutdown_signal)
        process.communicate(timeout=12)
        output = log_path.read_text()
        assert process.returncode in (0, -shutdown_signal), output
        assert "Traceback" not in output, output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        output_file.close()


def wait_for_file(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, f"Missing {path.name}"
        time.sleep(0.02)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM")
def test_sigterm_restarts_task_once_in_the_next_process(tmp_path):
    _write_configuration(tmp_path)
    with listener(tmp_path) as api:
        response = api.post("/api/threads", json={})
        assert response.status_code == 200, response.text
        thread_id = response.json()["thread_id"]
        response = api.post(f"/api/threads/{thread_id}/submit", json={"parts": ["Continue my work"]})
        assert response.status_code == 200, response.text
        wait_for_file(tmp_path / "root-started")
        # No prepare request, release file, or browser replay. The context sends
        # SIGTERM and waits for the real server's entire lifespan shutdown.
    with listener(tmp_path) as api:
        wait_for_file(tmp_path / "root-completed")
        deadline = time.monotonic() + 10
        while True:
            response = api.get(f"/api/threads/{thread_id}/transcript")
            # The model's completion marker precedes durable history publication.
            # A continuation conflict is explicitly retryable while that settles.
            if response.status_code == 400:
                assert response.json()["error"]["code"] == "thread_history_continuation_changed", response.text
            else:
                assert response.status_code == 200, response.text
                if "continued" in response.text.lower():
                    break
            assert time.monotonic() < deadline
            time.sleep(0.02)
    requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert [request["returns"] for request in requests] == [0, 1]


def wait_for_lines(path, count):
    deadline = time.monotonic() + 10
    while not path.exists() or len(path.read_text().splitlines()) < count:
        assert time.monotonic() < deadline, f"Missing {count} lines in {path.name}"
        time.sleep(0.02)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGKILL")
def test_sigkill_recovers_saved_tool_results_without_replaying_uncertain_tools(tmp_path):
    _write_configuration(tmp_path)
    with listener(tmp_path, mode="safe", shutdown_signal=signal.SIGKILL) as api:
        for prompt in ("Finish once", "Start an uncertain tool"):
            created = api.post("/api/threads", json={})
            assert created.status_code == 200, created.text
            thread_id = created.json()["thread_id"]
            response = api.post(f"/api/threads/{thread_id}/submit", json={"parts": [prompt]})
            assert response.status_code == 200, response.text
        wait_for_lines(tmp_path / "safe-requests.jsonl", 1)
        wait_for_file(tmp_path / "tool-started")
    (tmp_path / "allow-completion").touch()
    with listener(tmp_path, mode="safe"):
        wait_for_file(tmp_path / "root-completed")
    requests = [json.loads(line)["returns"] for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert requests == [0, 1, 1]
    effects = [json.loads(line)["key"] for line in (tmp_path / "effects.jsonl").read_text().splitlines()]
    assert sorted(effects) == ["saved-effect", "uncertain-effect"]
