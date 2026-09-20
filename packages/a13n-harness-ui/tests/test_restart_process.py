"""Real SIGTERM -> exit -> same-data startup, without a preparation request."""

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
import yaml

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
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

root = Path(sys.argv[1])
mode = sys.argv[3]
owner = None
async def stream(messages, info):
    returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
    role = 'root' if mode == 'root' or 'delegate' in {t.name for t in info.function_tools} else 'child'
    with (root / 'requests.jsonl').open('a') as log:
        log.write(json.dumps({'role': role, 'returns': len(returned)}) + '\n')
    if role == 'root' and mode == 'family':
        if not returned:
            yield {0: DeltaToolCall(name='delegate', tool_call_id='delegate-once', json_args=json.dumps({'subagent_name':'agent-worker','prompt':'Bounded work'}))}
        elif len(returned) == 1:
            (root / 'parent-waiting').touch()
            yield {0: DeltaToolCall(name='wait_subagent', tool_call_id='wait-once', json_args='{"timeout_seconds":120}')}
        else:
            yield 'Parent continued'
            (root / 'root-completed').touch()
    elif not returned:
        (root / f'{role}-started').touch()
        await owner._restart.pause_requested.wait()
        yield {0: DeltaToolCall(name='store', tool_call_id='effect-once', json_args='{"key":"saved-effect","value":1}')}
    else:
        assert len([p for p in returned if p.tool_call_id == 'effect-once']) == 1
        yield 'Continued after graceful restart'
        (root / f'{role}-completed').touch()
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
def listener(root, mode):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _SERVER, str(root), str(port), mode],
        stdout=subprocess.PIPE,
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
            yield api
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=12)
        assert process.returncode in (0, -signal.SIGTERM), output
        assert "Traceback" not in output, output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def wait_for_file(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, f"Missing {path.name}"
        time.sleep(0.02)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM")
@pytest.mark.parametrize("mode", ["root", "family"])
def test_sigterm_restarts_tasks_once_across_three_processes(tmp_path, mode):
    _write_configuration(tmp_path)
    if mode == "family":
        parent_path = tmp_path / "agents/assistant.yaml"
        parent = yaml.safe_load(parent_path.read_text())
        parent["subagents"] = [{"agent": "agent-worker"}]
        parent_path.write_text(yaml.safe_dump(parent))
        (tmp_path / "agents/worker.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1",
                    "kind": "agent",
                    "id": "agent-worker",
                    "name": "Worker",
                    "model": "model-primary",
                }
            )
        )
    with listener(tmp_path, mode) as api:
        response = api.post("/api/threads", json={})
        assert response.status_code == 200, response.text
        thread_id = response.json()["thread_id"]
        response = api.post(f"/api/threads/{thread_id}/submit", json={"prompt": "Continue my work"})
        assert response.status_code == 200, response.text
        wait_for_file(tmp_path / ("child-started" if mode == "family" else "root-started"))
        if mode == "family":
            wait_for_file(tmp_path / "parent-waiting")
        # No prepare request, release file, or browser replay. The context sends
        # SIGTERM and waits for the real server's entire lifespan shutdown.
    with listener(tmp_path, mode) as api:
        wait_for_file(tmp_path / "root-completed")
        if mode == "family":
            wait_for_file(tmp_path / "child-completed")
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
    before = (tmp_path / "requests.jsonl").read_text()
    requests = [json.loads(line) for line in before.splitlines()]
    assert [r["returns"] for r in requests if r["role"] == "root"] == ([0, 1, 2] if mode == "family" else [0, 1])
    if mode == "family":
        assert [r["returns"] for r in requests if r["role"] == "child"] == [0, 1]
    with listener(tmp_path, mode):
        time.sleep(0.1)
    assert (tmp_path / "requests.jsonl").read_text() == before
