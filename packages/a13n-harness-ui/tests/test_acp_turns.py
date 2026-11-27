"""ACP turn admission, steering, cancellation, and teardown tests."""

from __future__ import annotations

import asyncio

import pytest

from .acp_test_support import PROFILE, _agent, _wait_for_marker
from .test_interactive import _seed


@pytest.mark.xdist_group("harness-ui-acp-cancel-registration")
@pytest.mark.anyio
async def test_session_cancel_fences_registration_after_capture(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.acp_server import AcpAgent
from a13n_harness_ui.root_execution import RootRunExecutor
from a13n_harness_ui.root_run import RootRunCoordinator

original_capture = RootRunExecutor.capture
original_publish = RootRunCoordinator._publish_change
original_cancel = AcpAgent.cancel

async def capture(self, **kwargs):
    admission = await original_capture(self, **kwargs)
    Path('captured').touch()
    while not Path('capture-release').exists():
        await asyncio.sleep(0.01)
    return admission

async def publish(self, operation, **kwargs):
    if operation.status.value == 'preparing':
        Path('registered').touch()
        await asyncio.Future()
    await original_publish(self, operation, **kwargs)

async def cancel(self, session_id, **kwargs):
    turn = self.turns[session_id]
    task = asyncio.create_task(original_cancel(self, session_id, **kwargs))
    while not turn.cancel_requested:
        await asyncio.sleep(0)
    Path('cancel-latched').touch()
    await task
    Path('cancel-resolved').touch()

RootRunExecutor.capture = capture
RootRunCoordinator._publish_change = publish
AcpAgent.cancel = cancel
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        await _wait_for_marker(tmp_path / "captured")
        await rpc.send("session/cancel", sessionId=session_id)
        await _wait_for_marker(tmp_path / "cancel-latched")
        (tmp_path / "capture-release").touch()
        await _wait_for_marker(tmp_path / "cancel-resolved", timeout=8)
        reply, _ = await rpc.response(3)
        assert reply["result"]["stopReason"] == "cancelled", reply
        assert not (tmp_path / "registered").exists(), "A cancelled prompt registered a Run"


@pytest.mark.xdist_group("harness-ui-acp-eof-joins-run")
@pytest.mark.anyio
async def test_eof_releases_handlers_but_app_joins_stubborn_run(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from acp.agent import AgentSideConnection
from anyio import CancelScope
from a13n_harness_ui.app import HarnessUiApp

original_stop = HarnessUiApp._stop
original_close = AgentSideConnection.close

async def stop(self, **kwargs):
    Path('app-stopping').touch()
    await original_stop(self, **kwargs)

async def close(self):
    await original_close(self)
    Path('sdk-closed').touch()

async def stubborn(messages, info):
    Path('run-started').touch()
    try:
        await asyncio.Future()
        yield 'unreachable'
    finally:
        with CancelScope(shield=True):
            Path('run-cleaning').touch()
            while not Path('cleanup-release').exists():
                await asyncio.sleep(0.01)
            Path('cleanup-finished').touch()

HarnessUiApp._stop = stop
AgentSideConnection.close = close
codex.CodexRequestModel = lambda *a, **kw: FunctionModel(stream_function=stubborn)
"""
    async with _agent(path, tmp_path, hooks=hooks, discard_stdout=True) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "start"}])
        await _wait_for_marker(tmp_path / "run-started")
        await rpc.send("session/cancel", sessionId=session_id)
        try:
            await _wait_for_marker(tmp_path / "run-cleaning")
            rpc.process.stdin.close()
            await _wait_for_marker(tmp_path / "app-stopping", timeout=8)
            await _wait_for_marker(tmp_path / "sdk-closed", timeout=8)
            assert rpc.process.returncode is None, "Process exited before Run cleanup finished"
        finally:
            (tmp_path / "cleanup-release").touch()
        await _wait_for_marker(tmp_path / "cleanup-finished", timeout=8)
        await asyncio.wait_for(rpc.process.wait(), 15)
        assert rpc.process.returncode == 0


@pytest.mark.xdist_group("harness-ui-acp-rpc-cancel")
@pytest.mark.anyio
async def test_rpc_cancellation_after_registration_joins_run_before_releasing_turn(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.root_run import RootRunCoordinator

original_publish = RootRunCoordinator._publish_change
registered = Path('registered')

async def publish(self, operation, **kwargs):
    if operation.status.value == 'preparing' and not registered.exists():
        assert self._active_by_thread[operation.receipt.thread_id] == operation.receipt.receipt_id
        registered.touch()
        # The operation was started but the submitting task has not received its RPC result.
        await asyncio.Future()
    await original_publish(self, operation, **kwargs)

RootRunCoordinator._publish_change = publish
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "wait here"}])
        await _wait_for_marker(tmp_path / "registered")
        await rpc.send("$/cancel_request", requestId=3)
        reply, _ = await rpc.response(3)
        assert reply["error"]["code"] == -32800, reply
        # The same session accepts a new Run immediately, without needing session/cancel.
        await rpc.send("session/prompt", id=4, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        reply, _ = await rpc.response(4)
        assert reply["result"]["stopReason"] == "end_turn", reply


@pytest.mark.xdist_group("harness-ui-acp-resume-admission")
@pytest.mark.anyio
async def test_resume_and_prompt_cannot_overlap_session_admission(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from contextlib import asynccontextmanager
from a13n_harness_ui.app import HarnessUiApp

original_watch = HarnessUiApp.watch_thread
original_active = HarnessUiApp.active_root_operation

async def pause(name):
    flag = Path(f'{name}-reserved')
    if not flag.exists():
        flag.touch()
        while not Path(f'{name}-release').exists():
            await asyncio.sleep(0.01)

async def active(self, thread_id):
    result = await original_active(self, thread_id)
    await pause('resume')
    return result

@asynccontextmanager
async def watch(self, **kwargs):
    await pause('prompt')
    async with original_watch(self, **kwargs) as subscription:
        yield subscription

HarnessUiApp.active_root_operation = active
HarnessUiApp.watch_thread = watch
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        resume = {"sessionId": session_id, "cwd": str(tmp_path), "mcpServers": []}
        prompt = {"sessionId": session_id, "prompt": [{"type": "text", "text": "model check"}]}
        await rpc.send("session/resume", id=3, **resume)
        await _wait_for_marker(tmp_path / "resume-reserved")
        await rpc.send("session/prompt", id=4, **prompt)
        reply, _ = await rpc.response(4)
        assert reply["error"]["code"] == -32600, reply
        (tmp_path / "resume-release").touch()
        reply, _ = await rpc.response(3)
        assert "result" in reply, reply

        await rpc.send("session/set_config_option", id=5, sessionId=session_id, configId="thought_level", value="low")
        reply, _ = await rpc.response(5)
        assert "result" in reply, reply
        await rpc.send("session/prompt", id=6, **prompt)
        await _wait_for_marker(tmp_path / "prompt-reserved")
        await rpc.send("session/resume", id=7, **resume)
        reply, _ = await rpc.response(7)
        assert reply["error"]["code"] == -32600, reply
        (tmp_path / "prompt-release").touch()
        reply, _ = await rpc.response(6)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "gpt-5.6-sol low", reply


@pytest.mark.xdist_group("harness-ui-acp-steer-and-cancel")
@pytest.mark.anyio
async def test_steer_and_cancel_exact_open_turn(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    async with _agent(path, tmp_path, unattended=True) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]

        async def start_turn(rpc_id, turn_id):
            await rpc.send(
                "session/prompt",
                id=rpc_id,
                sessionId=session_id,
                prompt=[{"type": "text", "text": "wait here"}],
                _meta={PROFILE: {"turnId": turn_id}},
            )
            while (frame := await rpc.receive()).get("method") != "session/update":
                assert frame.get("id") != rpc_id, frame

        await start_turn(3, "steer")
        await rpc.send("_a13n/steer", id=4, sessionId=session_id, turnId="wrong", inputId="wrong", text="wrong")
        reply, _ = await rpc.response(4)
        assert reply["result"]["delivery"] == "not_delivered"
        await rpc.send("_a13n/steer", id=5, sessionId=session_id, turnId="steer", inputId="steer", text="steer now")
        messages = []
        while {frame.get("id") for frame in messages} & {3, 5} != {3, 5}:
            messages.append(await rpc.receive())
        assert next(m for m in messages if m.get("id") == 5)["result"]["delivery"] == "accepted"
        terminal = next(i for i, m in enumerate(messages) if m.get("id") == 3)
        assert messages[terminal]["result"]["stopReason"] == "end_turn"
        assert any(
            m.get("method") == "_a13n/input_accepted" and m["params"]["inputId"] == "steer" for m in messages[:terminal]
        )

        # Use a separate session so that the previous turn's steering is not in the fake provider's history.
        await rpc.send("session/new", id=6, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(6)
        session_id = reply["result"]["sessionId"]
        await start_turn(7, "cancel")
        await rpc.send("session/cancel", sessionId=session_id)
        reply, _ = await rpc.response(7)
        assert reply.get("result", {}).get("stopReason") == "cancelled", reply
        await rpc.send("_a13n/steer", id=8, sessionId=session_id, turnId="cancel", inputId="late", text="late")
        reply, _ = await rpc.response(8)
        assert reply["result"]["delivery"] == "not_delivered"
