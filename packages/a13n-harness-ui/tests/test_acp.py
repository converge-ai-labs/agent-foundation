"""ACP wire and turn-lifecycle contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from a13n_harness.context import AgentContext
from a13n_harness_ui.acp_server import AcpAgent, _Approval, _Turn
from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.live import LiveSubscription
from a13n_harness_ui.surfaces import RootRunReceipt
from acp import Client
from acp.schema import AgentThoughtChunk, RequestPermissionResponse, TextContentBlock
from pydantic_ai import Agent, DeferredToolRequests, RunContext, Tool
from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolApproved, ToolDenied

from .test_interactive import _seed

PROFILE = "dev.a13n.harness-ui"


def test_acp_rejects_windows_before_app_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.cli as cli_module
    from click.testing import CliRunner

    def unexpected_start(request):
        raise AssertionError("ACP must not start an App on Windows")

    monkeypatch.setattr(cli_module, "system", lambda: "Windows")
    monkeypatch.setattr(cli_module, "_execute", unexpected_start)
    result = CliRunner().invoke(cli_module.cli, ["acp"])
    assert result.exit_code == 1
    assert not result.stdout
    assert result.stderr == "Error: ACP over stdio is not supported on Windows.\n"


_FAKE = """
import asyncio
import json
from functools import partial
from pathlib import Path
import a13n_harness.models.codex as codex
from a13n_harness.capabilities.tool_review import ToolReviewAssessment, ToolReviewResult
from a13n_harness.tools.permissions import ToolPermissionsCapability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.messages import ToolCallPart, ToolReturnPart, UserPromptPart
from a13n_harness_ui.cli import main

async def review(self, request, *, context):
    return ToolReviewResult(assessment=ToolReviewAssessment(risk="extra_high", reason="flagged by test reviewer"))

ToolPermissionsCapability.review = review

async def stream(model, messages, info):
    prompts = [str(p.content) for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
    if any("model check" in p for p in prompts):
        yield f"{model} {info.model_request_parameters.thinking}"
        return
    if any("shell check" in p for p in prompts):
        Path('shell-tool-surface').write_text(json.dumps(sorted(t.name for t in info.function_tools)))
        Path('shell-instructions').write_text(info.instructions or '')
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "shell_exec"]
        if not returns:
            command = f"printf '%s' ran > '{Path.cwd() / 'shell-marker'}'"
            yield {0: DeltaToolCall(name="shell_exec", json_args=json.dumps({"command": command}))}
            return
        result = str(returns[0].content)
        if "denied in unattended mode" in result:
            yield "denied automatically"
        elif returns[0].outcome == "denied":
            yield "denied by client"
        else:
            yield "command finished"
        return
    if any("wait here" in p for p in prompts) and not any("steer now" in p for p in prompts):
        yield "waiting"
        await asyncio.sleep(30)
    tool_names = {t.name for t in info.function_tools}
    if any("show tools" in p for p in prompts):
        assert "post_to_inbox" not in tool_names, "WebUI Inbox tool was exposed in ACP"
    if any("show tools" in p for p in prompts) and "opt_echo" in tool_names:
        assert "opt_forbidden" not in tool_names, "Explicit ask escaped pre-exposure filtering"
        if not any(isinstance(p, ToolCallPart) and p.tool_name == 'opt_echo' for m in messages for p in m.parts):
            yield {0: DeltaToolCall(name="opt_echo", json_args="{}")}
            return
        if any(isinstance(p, ToolReturnPart) and "tool returned" in str(p.content) for m in messages for p in m.parts):
            yield "MCP worked"
            return
    yield "final answer"

codex.CodexRequestModel = lambda name, **kw: FunctionModel(
    stream_function=partial(stream, name), profile={"supports_thinking": True}
)
main()
"""


@pytest.mark.anyio
async def test_acp_rejects_nonordinary_resume_and_dangling_model(tmp_path):
    from types import SimpleNamespace
    from typing import cast

    from a13n_harness_ui.acp_server import AcpAgent
    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.surfaces import RunModelOverrides
    from acp import RequestError

    thread = SimpleNamespace(
        role="coordinator",
        coordinator_thread_id=None,
        parent_thread_id=None,
        archived=False,
        memory_scope=None,
        configuration=SimpleNamespace(agent_source=SimpleNamespace(kind="agent")),
    )

    class App:
        async def get_thread(self, thread_id):
            return SimpleNamespace(thread=thread)

        async def current_configuration(self):
            return SimpleNamespace(models={"available": SimpleNamespace(name="Available")})

        async def inspect_thread_configuration(self, thread_id):
            return SimpleNamespace(next_model_id="removed")

    agent = AcpAgent(cast(HarnessUiApp, App()))
    await agent.initialize(1)
    with pytest.raises(RequestError) as error:
        await agent.resume_session("session", str(tmp_path))
    assert error.value.code == -32602
    thread.role = "ordinary"
    thread.configuration.agent_source.kind = "live"
    with pytest.raises(RequestError) as error:
        await agent.resume_session("session", str(tmp_path))
    assert error.value.code == -32602
    agent.model_overrides["session"] = RunModelOverrides(model_id="also-removed")
    (model_option,) = await agent._options("session")
    assert model_option.current_value == "default"
    assert model_option.current_value in {option.value for option in model_option.options}


class Rpc:
    def __init__(self, process: asyncio.subprocess.Process, stdin_peer: socket.socket | None = None):
        self.process = process
        self.stdin_peer = stdin_peer

    async def send_bytes(self, data: bytes) -> None:
        if self.stdin_peer is not None:
            await asyncio.get_running_loop().sock_sendall(self.stdin_peer, data)
        else:
            assert self.process.stdin is not None
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    def shutdown_input(self) -> None:
        if self.stdin_peer is not None:
            self.stdin_peer.shutdown(socket.SHUT_WR)
        else:
            assert self.process.stdin is not None
            self.process.stdin.transport.abort()

    async def send(self, method, *, id=None, **params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if id is not None:
            msg["id"] = id
        await self.send_bytes(json.dumps(msg).encode() + b"\n")

    async def receive(self):
        assert self.process.stdout is not None
        line = await asyncio.wait_for(self.process.stdout.readline(), 45)
        assert line, "Unexpected EOF on the ACP pipe"
        return json.loads(line)

    async def response(self, id):
        frames = []
        async with asyncio.timeout(60):
            while (frame := await self.receive()).get("id") != id:
                frames.append(frame)
                assert len(frames) <= 256
        return frame, frames


@asynccontextmanager
async def _agent(
    path: Path,
    tmp_path: Path,
    *,
    unattended: bool = False,
    hooks: str = "",
    discard_stdout: bool = False,
    init_meta: dict[str, str] | None = None,
    stdin_sockets: tuple[socket.socket, socket.socket] | None = None,
):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _FAKE.removesuffix("main()\n") + hooks + "\nmain()\n",
        "--config",
        str(path),
        "--data-root",
        str(tmp_path / "data"),
        "acp",
        *(("--unattended",) if unattended else ()),
        env=os.environ.copy(),
        cwd=tmp_path,
        stdin=stdin_sockets[1] if stdin_sockets else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=8192 if discard_stdout else 65536,
    )
    if stdin_sockets:
        stdin_sockets[1].close()
        stdin_sockets[0].setblocking(False)
    assert process.stderr and process.stdout
    stderr = asyncio.create_task(process.stderr.read())
    try:
        rpc = Rpc(process, stdin_sockets[0] if stdin_sockets else None)
        metadata = dict(init_meta or {})
        params = {"_meta": metadata} if metadata else {}
        await rpc.send("initialize", id=1, protocolVersion=1, clientCapabilities={}, **params)
        result, frames = await rpc.response(1)
        assert not frames
        assert result["result"]["protocolVersion"] == 1
        profile = result["result"]["_meta"][PROFILE]
        assert profile == {"version": 1, "unattended": unattended}
        assert "resume" in result["result"]["agentCapabilities"]["sessionCapabilities"]
        yield rpc
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if stdin_sockets and process.returncode is None:
            stdin_sockets[0].close()
        stdout = asyncio.create_task(process.stdout.read()) if discard_stdout else None
        try:
            await asyncio.wait_for(process.wait(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
        if stdout is not None:
            await stdout
        diagnostics = (await stderr).decode()
        if sys.exc_info()[0] is not None:
            print(diagnostics)
        rpc.diagnostics = diagnostics
        assert process.returncode == 0, diagnostics
        assert not await process.stdout.read(), "Unexpected trailing stdout"


async def _wait_for_marker(path: Path, *, timeout: float = 45) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_large_initialize_during_app_startup_keeps_connection(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.acp_stdio import _StdinReader
from a13n_harness_ui.root_run import RootRunCoordinator

original_start = RootRunCoordinator.start
original_feed_data = _StdinReader.feed_data
input_paused = asyncio.Event()

def feed_data(self, data):
    original_feed_data(self, data)
    if len(self._buffer) > 2 * self._limit:
        input_paused.set()

async def start(self):
    # The SDK listener starts after App initialization. Make sure normal
    # StreamReader backpressure is already in effect at that point.
    await asyncio.wait_for(input_paused.wait(), 10)
    return await original_start(self)

_StdinReader.feed_data = feed_data
RootRunCoordinator.start = start
"""
    async with _agent(path, tmp_path, hooks=hooks, init_meta={"workspaceContext": "x" * (3 * 1024 * 1024)}) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        assert reply["result"]["sessionId"]


@pytest.mark.anyio
async def test_valid_input_burst_keeps_connection_while_handler_waits(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.acp_server import AcpAgent
from a13n_harness_ui.acp_stdio import _StdinReader

original_inventory = AcpAgent._inventory
original_readuntil = _StdinReader.readuntil
original_feed_data = _StdinReader.feed_data
waiting = Path('handler-waiting')
gate_next_read = False

async def readuntil(self, separator):
    global gate_next_read
    if gate_next_read:
        while not Path('reader-release').exists():
            await asyncio.sleep(0.01)
    line = await original_readuntil(self, separator)
    if line and json.loads(line).get('id') == 3:
        gate_next_read = True
    return line

def feed_data(self, data):
    original_feed_data(self, data)
    if self._pipe is not None and not self._pipe.is_reading():
        Path('reader-paused').touch()

async def inventory(self, session_id):
    if not waiting.exists():
        waiting.touch()
        while not Path('handler-release').exists():
            await asyncio.sleep(0.02)
    return await original_inventory(self, session_id)

AcpAgent._inventory = inventory
_StdinReader.readuntil = readuntil
_StdinReader.feed_data = feed_data
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("_a13n/inventory", id=3, sessionId=session_id)
        await _wait_for_marker(tmp_path / "handler-waiting", timeout=10)
        ids = set(range(4, 12))
        burst = b"".join(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": id,
                    "method": "_a13n/inventory",
                    "params": {
                        "sessionId": session_id,
                        "_meta": {"workspaceContext": "x" * (320 * 1024)},
                    },
                }
            ).encode()
            + b"\n"
            for id in sorted(ids)
        )
        sender = asyncio.create_task(rpc.send_bytes(burst))
        try:
            await _wait_for_marker(tmp_path / "reader-paused", timeout=10)
            (tmp_path / "reader-release").touch()
            await asyncio.wait_for(sender, 10)
        finally:
            if not sender.done():
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        for _ in range(len(ids)):
            reply = await rpc.receive()
            assert "result" in reply, reply
            ids.remove(reply["id"])
        assert not ids
        (tmp_path / "handler-release").touch()
        reply, _ = await rpc.response(3)
        assert "result" in reply, reply


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


@pytest.mark.skipif(os.name == "nt", reason="Exercises POSIX stdio backpressure")
@pytest.mark.parametrize("use_socket", [False, True], ids=["pipe", "unix-socket-half-close"])
@pytest.mark.anyio
async def test_eof_aborts_backpressured_stdout(tmp_path, monkeypatch, use_socket):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.acp_server import AcpAgent
from a13n_harness_ui.acp_stdio import _StdinReader
from a13n_harness_ui.app import HarnessUiApp

original_drain = asyncio.StreamWriter.drain
original_readuntil = _StdinReader.readuntil
original_feed_data = _StdinReader.feed_data
original_stop = HarnessUiApp._stop

async def drain(self):
    _, high = self.transport.get_write_buffer_limits()
    if self.transport.get_write_buffer_size() > high:
        Path('stdout-blocked').touch()
    await original_drain(self)

async def inventory(self, session_id):
    return {'fixture': 'x' * (1024 * 1024)}

async def readuntil(self, separator):
    line = await original_readuntil(self, separator)
    if line == b'not JSON\\n':
        Path('parse-error-read').touch()
    return line

def feed_data(self, data):
    original_feed_data(self, data)
    if len(self._buffer) > 2 * self._limit:
        Path('stdin-high-water').touch()

async def stop(self, **kwargs):
    Path('app-stopping').touch()
    await original_stop(self, **kwargs)

asyncio.StreamWriter.drain = drain
AcpAgent._inventory = inventory
_StdinReader.readuntil = readuntil
_StdinReader.feed_data = feed_data
HarnessUiApp._stop = stop
"""
    with ExitStack() as resources:
        sockets = None
        if use_socket:
            peer, child = socket.socketpair()
            sockets = resources.enter_context(peer), resources.enter_context(child)
        async with _agent(path, tmp_path, hooks=hooks, discard_stdout=True, stdin_sockets=sockets) as rpc:
            await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
            reply, _ = await rpc.response(2)
            await rpc.send("_a13n/inventory", id=3, sessionId=reply["result"]["sessionId"])
            await _wait_for_marker(tmp_path / "stdout-blocked")
            # Pause stdin while the SDK waits to write its parse error.
            await rpc.send_bytes(b"not JSON\n")
            await _wait_for_marker(tmp_path / "parse-error-read", timeout=8)
            sender = asyncio.create_task(rpc.send_bytes(b"\n" * (4 * 1024 * 1024)))
            try:
                await _wait_for_marker(tmp_path / "stdin-high-water", timeout=8)
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            # Do not flush past high water or close the socket's read side.
            rpc.shutdown_input()
            await _wait_for_marker(tmp_path / "app-stopping", timeout=8)
            # Keep stdout unread until child exit. Process.wait() also waits
            # for the parent's stdout transport, so observe the exit directly.
            async with asyncio.timeout(8):
                while rpc.process.returncode is None:
                    await asyncio.sleep(0.02)
            assert rpc.process.returncode == 0


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


@pytest.mark.anyio
async def test_unknown_usage_window_reports_in_extension_notification(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui.app import HarnessUiApp

original_usage = HarnessUiApp.context_usage

async def context_usage(self, thread_id):
    context = await original_usage(self, thread_id)
    return context.model_copy(update={'context_window': None})

HarnessUiApp.context_usage = context_usage
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "hello"}])
        reply, frames = await rpc.response(3)
        assert reply["result"]["stopReason"] == "end_turn", reply
        updates = [
            frame["params"]["update"]["sessionUpdate"] for frame in frames if frame.get("method") == "session/update"
        ]
        assert "notice" not in updates and "usage_update" not in updates
        reports = [frame["params"] for frame in frames if frame.get("method") == "_a13n/usage_report"]
        assert reports
        for report in reports:
            assert report["version"] == 1 and report["sessionId"] == session_id and report["usageReport"]["records"]


@pytest.mark.anyio
async def test_new_prompt_acceptance_stream_and_resume(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    model_path = path.parent / "models" / "codex.yaml"
    model_source = model_path.read_text()
    (model_path.parent / "second.yaml").write_text(
        model_source.replace("model-codex", "model-second").replace("gpt-5.6-sol", "gpt-5.6-luna")
    )
    async with _agent(path, tmp_path) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send(
            "session/prompt",
            id=3,
            sessionId=session_id,
            prompt=[{"type": "text", "text": "hello"}],
            _meta={PROFILE: {"inputIds": ["abc"]}},
        )
        reply, frames = await rpc.response(3)
        assert reply.get("result", {}).get("stopReason") == "end_turn", reply
        final = reply["result"]["_meta"][PROFILE]
        assert final["finalOutput"] == "final answer"
        assert final["finalOutputOmitted"] is False
        assert len(final["finalMessage"]["continuationId"]) == 64
        assert final["finalMessage"]["position"] > 0
        assert any(
            frame["method"] == "_a13n/input_accepted" and frame["params"]["inputId"] == "abc" for frame in frames
        )
        assert any(
            frame["method"] == "session/update"
            and frame["params"]["update"].get("sessionUpdate") == "agent_message_chunk"
            for frame in frames
        )
        assert any(
            "usageReport" in f.get("params", {}).get("update", {}).get("_meta", {}).get(PROFILE, {}) for f in frames
        )
        await rpc.send("_a13n/steer", id=4, sessionId=session_id, turnId="first", inputId="late", text="too late")
        reply, _ = await rpc.response(4)
        assert reply["result"]["delivery"] == "not_delivered"
        await rpc.send("_a13n/inventory", id=5, sessionId=session_id)
        reply, _ = await rpc.response(5)
        assert reply["result"]["defaultModel"] == "model-codex"
        assert {model["id"] for model in reply["result"]["models"]} == {"model-codex", "model-second"}
        for id, option, value in [(6, "model", "model-second"), (7, "thought_level", "low")]:
            await rpc.send("session/set_config_option", id=id, sessionId=session_id, configId=option, value=value)
            reply, _ = await rpc.response(id)
            assert any(o["id"] == option and o["currentValue"] == value for o in reply["result"]["configOptions"])
        await rpc.send("session/prompt", id=8, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        reply, _ = await rpc.response(8)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "gpt-5.6-luna low"
    async with _agent(path, tmp_path) as rpc:
        await rpc.send("session/resume", id=2, sessionId=session_id, cwd=str(other), mcpServers=[])
        reply, _ = await rpc.response(2)
        assert "error" in reply
        await rpc.send("session/resume", id=3, sessionId=session_id, cwd=str(tmp_path), mcpServers=[])
        reply, frames = await rpc.response(3)
        assert "result" in reply and not frames
        assert {option["id"]: option["currentValue"] for option in reply["result"]["configOptions"]} == {
            "model": "model-codex",
            "thought_level": "high",
        }
        await rpc.send("session/prompt", id=4, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        reply, _ = await rpc.response(4)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "gpt-5.6-sol high"
        assert reply["result"]["_meta"][PROFILE]["finalMessage"]["position"] > final["finalMessage"]["position"]
    assert model_source == model_path.read_text()


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


def _approval_turn(**app_methods):
    agent = AcpAgent(cast(HarnessUiApp, SimpleNamespace(**app_methods)))
    turn = _Turn(
        "turn", receipt=RootRunReceipt(receipt_id="receipt", thread_id="session", submitted_at=datetime.now(UTC))
    )
    agent.turns["session"] = turn
    return agent, turn


async def _request_approval(agent: AcpAgent, turn: _Turn):
    call = ToolCallPart("example_tool", {}, "call")
    return await agent._permission_handler(turn)(
        cast(RunContext[AgentContext], None), DeferredToolRequests(approvals=[call])
    )


@pytest.mark.anyio
async def test_disconnect_denies_approval_queued_behind_blocked_update():
    update_started = asyncio.Event()
    blocked = asyncio.Event()

    async def session_update(*args):
        update_started.set()
        await blocked.wait()

    async def wait_for_teardown(*args):
        await blocked.wait()
        raise AssertionError("Expected transport teardown to abandon the observer")

    agent, turn = _approval_turn(wait_root_operation=wait_for_teardown)
    agent.on_connect(cast(Client, SimpleNamespace(session_update=session_update)))
    turn.notifications.put_nowait(AgentThoughtChunk(content=TextContentBlock(type="text", text="thinking")))
    events = cast(LiveSubscription, SimpleNamespace(receive=wait_for_teardown))
    observer = asyncio.create_task(agent._stream("session", turn, events))
    tasks = [observer]
    try:
        async with asyncio.timeout(3):
            await update_started.wait()
            approval = asyncio.create_task(_request_approval(agent, turn))
            tasks.append(approval)
            await asyncio.sleep(0)  # Queue the approval while the output is blocked.
            assert turn.notifications.qsize() == 1
            assert not approval.done()
            agent.disconnect()
            assert (await approval).approvals == {"call": ToolDenied()}
            assert not observer.done()
            assert not turn.approvals
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_cancel_denies_late_allow_before_app_stops_run():
    sent = asyncio.Event()
    late_reply = asyncio.Event()
    stop_started = asyncio.Event()
    blocked = asyncio.Event()
    allow = asyncio.get_running_loop().create_future()

    async def session_update(*args):
        pass

    async def request_permission(*args):
        sent.set()
        response = await allow
        late_reply.set()
        return response

    async def cancel_root_operation(*args):
        stop_started.set()
        await blocked.wait()

    agent, turn = _approval_turn(cancel_root_operation=cancel_root_operation)
    agent.on_connect(
        cast(Client, SimpleNamespace(session_update=session_update, request_permission=request_permission))
    )
    approval = asyncio.create_task(_request_approval(agent, turn))
    tasks = [approval]
    try:
        async with asyncio.timeout(3):
            request = await turn.notifications.get()
            observer = asyncio.create_task(agent._notify("session", turn, request))
            tasks.append(observer)
            await sent.wait()
            # Make the reply runnable but latch cancellation before yielding to it.
            allow.set_result(
                RequestPermissionResponse.model_validate({"outcome": {"outcome": "selected", "optionId": "allow-once"}})
            )
            await agent.cancel("session")
            assert turn.stopping is not None
            tasks.append(turn.stopping)
            await stop_started.wait()
            await late_reply.wait()
            assert (await approval).approvals == {"call": ToolDenied()}
            await observer
            assert not turn.stopping.done()
            assert not turn.approvals
    finally:
        agent.disconnect()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("disconnect", [False, True], ids=["cancel", "disconnect"])
async def test_turn_stop_denies_entire_approval_batch(disconnect: bool):
    blocked = asyncio.Event()
    calls_run: list[str] = []

    async def cancel_root_operation(*args):
        await blocked.wait()

    async def action(name: str) -> str:
        calls_run.append(name)
        return name

    async def model(messages, info):
        returns = {part.tool_call_id: part.outcome for part in messages[-1].parts if isinstance(part, ToolReturnPart)}
        if returns:
            assert returns == {"first": "denied", "second": "denied"}
            return ModelResponse(parts=[TextPart("Done")])
        return ModelResponse(
            parts=[ToolCallPart("action", {"name": call_id}, call_id) for call_id in ("first", "second")]
        )

    agent, turn = _approval_turn(cancel_root_operation=cancel_root_operation)
    runner = Agent(
        FunctionModel(model),
        deps_type=AgentContext,
        tools=[Tool(action, requires_approval=True)],
        capabilities=[HandleDeferredToolCalls(agent._permission_handler(turn))],
    )
    run = asyncio.create_task(runner.run("Try two actions"))
    try:
        async with asyncio.timeout(3):
            first = await turn.notifications.get()
            assert isinstance(first, _Approval) and first.call.tool_call_id == "first"
            first.answer.set_result(ToolApproved())
            second = await turn.notifications.get()
            assert isinstance(second, _Approval) and second.call.tool_call_id == "second"
            assert not second.answer.done()
            if disconnect:
                agent.disconnect()
            else:
                await agent.cancel("session")
            assert second.answer.result() == ToolDenied()
            assert (await run).output == "Done"
            assert not calls_run
    finally:
        agent.disconnect()
        run.cancel()
        tasks = [run] if turn.stopping is None else [run, turn.stopping]
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_approval_requested_after_disconnect_is_denied_without_queueing():
    agent, turn = _approval_turn()
    agent.disconnect()
    async with asyncio.timeout(3):
        assert (await _request_approval(agent, turn)).approvals == {"call": ToolDenied()}
    assert turn.notifications.empty()
    assert not turn.approvals


@pytest.mark.anyio
async def test_permission_choices_and_cancel_pending_interactive_request(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch, shell_review=True)
    marker = tmp_path / "shell-marker"
    async with _agent(path, tmp_path) as rpc:
        for index, choice in enumerate(("reject-once", "allow-once", None)):
            new_id = index * 2 + 2
            prompt_id = new_id + 1
            await rpc.send("session/new", id=new_id, cwd=str(tmp_path), mcpServers=[])
            reply, _ = await rpc.response(new_id)
            session_id = reply["result"]["sessionId"]
            await rpc.send(
                "session/prompt", id=prompt_id, sessionId=session_id, prompt=[{"type": "text", "text": "shell check"}]
            )
            starts = set()
            while (frame := await rpc.receive()).get("method") != "session/request_permission":
                assert frame.get("id") != prompt_id, frame
                update = frame.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "tool_call":
                    starts.add(update["toolCallId"])
            params = frame["params"]
            assert params["toolCall"]["toolCallId"] in starts
            assert isinstance(params["toolCall"]["rawInput"]["command"], str)
            assert params["sessionId"] == session_id
            assert params["toolCall"]["toolCallId"]
            assert {option["kind"] for option in params["options"]} == {"allow_once", "reject_once"}
            permission_id = frame["id"]
            if choice is None:
                await rpc.send("session/cancel", sessionId=session_id)
                received = []
                while not (
                    any(f.get("id") == prompt_id for f in received)
                    and any(f.get("method") == "$/cancel_request" for f in received)
                ):
                    received.append(await rpc.receive())
                    assert len(received) < 64, received
                assert next(f["result"]["stopReason"] for f in received if f.get("id") == prompt_id) == "cancelled"
                assert (
                    next(f["params"]["requestId"] for f in received if f.get("method") == "$/cancel_request")
                    == permission_id
                )
                updates = [frame.get("params", {}).get("update", {}) for frame in received]
                assert any(
                    update.get("sessionUpdate") == "tool_call_update"
                    and update.get("toolCallId") == params["toolCall"]["toolCallId"]
                    and update.get("status") == "failed"
                    for update in updates
                )
                assert not marker.exists()
                continue
            rpc.process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": permission_id,
                        "result": {"outcome": {"outcome": "selected", "optionId": choice}},
                    }
                ).encode()
                + b"\n"
            )
            await rpc.process.stdin.drain()
            reply, _ = await rpc.response(prompt_id)
            allowed = choice == "allow-once"
            assert reply["result"]["_meta"][PROFILE]["finalOutput"] == (
                "command finished" if allowed else "denied by client"
            )
            assert marker.exists() is allowed
            if allowed:
                marker.unlink()


@pytest.mark.anyio
async def test_checkpoint_finishes_when_client_disconnects_after_selection(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
from a13n_harness_ui import acp_server
from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.restart import GracefulRestart
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.root_execution import RootRunExecutor
from a13n_harness_ui.root_run import RootRunCoordinator

original_persist = RootRunCoordinator._persist_execution
original_select = RootRunExecutor._select_state
original_boundary = GracefulRestart.boundary
original_checkpoint = RootCheckpointCapability._checkpoint
original_stop = HarnessUiApp._stop

async def select(self, **kwargs):
    selected = await original_select(self, **kwargs)
    if not Path('primary-selected').exists():
        assert selected.status == 'selected'
        summary = self._summary_hub
        assert summary is not None
        original_publish = type(summary).publish
        async def publish(hub, **kw):
            await original_publish(hub, **kw)
            if Path('restart-boundary').exists():
                Path('summary-published').touch()
        type(summary).publish = publish
        Path('primary-selected').touch()
        while not Path('release-primary').exists():
            await asyncio.sleep(0.01)
    return selected

async def boundary(self, item):
    await original_boundary(self, item)
    Path('restart-boundary').touch()

async def checkpoint(self, ctx, state):
    await original_checkpoint(self, ctx, state)
    assert Path('summary-published').exists()
    Path('checkpoint-emitted').touch()

async def stop(self, **kwargs):
    Path('app-stopping').touch()
    await original_stop(self, **kwargs)

async def persist(self, operation):
    await original_persist(self, operation)
    if operation.completed_at is not None:
        Path('settled-status').write_text(operation.status.value)

RootRunCoordinator._persist_execution = persist
RootRunExecutor._select_state = select
GracefulRestart.boundary = boundary
RootCheckpointCapability._checkpoint = checkpoint
HarnessUiApp._stop = stop
"""
    async with _agent(path, tmp_path, hooks=hooks, discard_stdout=True) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        await rpc.send(
            "session/prompt",
            id=3,
            sessionId=reply["result"]["sessionId"],
            prompt=[{"type": "text", "text": "continue"}],
            _meta={PROFILE: {"inputIds": ["initial"]}},
        )
        try:
            await _wait_for_marker(tmp_path / "primary-selected")
            rpc.shutdown_input()
            await _wait_for_marker(tmp_path / "app-stopping", timeout=8)
        finally:
            (tmp_path / "release-primary").touch()
        await _wait_for_marker(tmp_path / "checkpoint-emitted", timeout=8)
    assert (tmp_path / "settled-status").read_text() == "cancelled"


@pytest.mark.parametrize("backpressure", [False, True])
@pytest.mark.anyio
async def test_wrapped_summary_and_run_progress_under_backpressure(tmp_path, monkeypatch, backpressure):
    path = await _seed(tmp_path, monkeypatch)
    size = 256 * 1024 if backpressure else 0
    hooks = f"""
from a13n_harness_ui import acp_server
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from pydantic_ai.models.function import DeltaThinkingPart
from pydantic_ai.models.wrapper import WrapperModel

acp_server.CodexRequestModel = FunctionModel
original = RootCheckpointCapability._checkpoint

async def checkpoint(self, ctx, state):
    await original(self, ctx, state)
    Path('checkpoint-emitted').touch()

async def thinking(messages, info):
    yield {{0: DeltaThinkingPart(content='public summary' + 'x' * {size}, signature='private signature')}}
    yield 'final answer'
    await asyncio.sleep(0.1)
    Path('model-finished').touch()

RootCheckpointCapability._checkpoint = checkpoint
codex.CodexRequestModel = lambda *a, **kw: WrapperModel(WrapperModel(FunctionModel(stream_function=thinking)))
"""
    async with _agent(path, tmp_path, hooks=hooks, discard_stdout=backpressure) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        await rpc.send(
            "session/prompt",
            id=3,
            sessionId=reply["result"]["sessionId"],
            prompt=[{"type": "text", "text": "think"}],
            _meta={PROFILE: {"inputIds": ["initial"]}},
        )
        if backpressure:
            await _wait_for_marker(tmp_path / "checkpoint-emitted")
            await _wait_for_marker(tmp_path / "model-finished", timeout=8)
        else:
            reply, frames = await rpc.response(3)
            assert reply["result"]["stopReason"] == "end_turn", reply
            summaries = [
                update["content"]["text"]
                for frame in frames
                if (update := frame.get("params", {}).get("update", {})).get("sessionUpdate") == "agent_thought_chunk"
            ]
            assert summaries == ["public summary"]


@pytest.mark.parametrize("acp", [False, True])
@pytest.mark.anyio
async def test_default_unattended_surface_and_shell_review(tmp_path, monkeypatch, acp):
    path = await _seed(tmp_path, monkeypatch, shell_review=True)
    root = yaml.safe_load(path.read_text())
    root["subagents"] = {
        "dynamic": {"enabled": True, "allowed_models": ["model-codex"], "default_model": "model-codex"}
    }
    path.write_text(yaml.safe_dump(root))
    if acp:
        async with _agent(path, tmp_path, unattended=True) as rpc:
            await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
            reply, _ = await rpc.response(2)
            session_id = reply["result"]["sessionId"]
            await rpc.send(
                "session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "shell check"}]
            )
            reply, _ = await rpc.response(3)
            assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "denied automatically"
            await rpc.send("_a13n/inventory", id=4, sessionId=session_id)
            reply, _ = await rpc.response(4)
            assert isinstance(reply["result"]["omittedTools"], list)
    else:
        await _one_shot_shell_check(path, tmp_path)
    names = set(json.loads((tmp_path / "shell-tool-surface").read_text()))
    assert {
        "shell_exec",
        "shell_wait",
        "shell_signal",
        "shell_input",
        "tool_execution_info",
        "tool_execution_result",
        "tool_execution_wait",
        "tool_execution_cancel",
        "note_write",
        "run_code",
        "store",
        "load",
        "forget",
        "subagent_info",
        "wait_subagent",
        "steer_subagent",
        "cancel_subagent",
    } <= names, names
    assert "ask_user_question" not in names
    assert "ask_user_question" not in (tmp_path / "shell-instructions").read_text()
    assert not (tmp_path / "shell-marker").exists()


async def _one_shot_shell_check(path, tmp_path):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _FAKE,
        "--config",
        str(path),
        "--data-root",
        str(tmp_path / "data"),
        "run",
        "shell check",
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    assert b"denied automatically" in stdout


@pytest.mark.anyio
async def test_unsupported_external_input_fails_tool(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    hooks = """
async def ask(messages, info):
    returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
    if not returns:
        assert 'ask_user_question' in {tool.name for tool in info.function_tools}
        yield {0: DeltaToolCall(name='ask_user_question', json_args='{}')}
    else:
        assert returns[-1].outcome == 'failed', returns[-1]
        yield 'unsupported input failed'

codex.CodexRequestModel = lambda *a, **kw: FunctionModel(stream_function=ask)
"""
    async with _agent(path, tmp_path, hooks=hooks) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        await rpc.send(
            "session/prompt", id=3, sessionId=reply["result"]["sessionId"], prompt=[{"type": "text", "text": "ask"}]
        )
        reply, frames = await rpc.response(3)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "unsupported input failed"
        assert not any(frame.get("method") == "session/request_permission" for frame in frames)


@pytest.mark.anyio
async def test_unattended_session_mcp_is_explicit_ephemeral_and_reported(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    agent = next((path.parent / "agents").glob("*.yaml"))
    agent.write_text(
        agent.read_text()
        + """\
- capability: ToolPermissionsCapability
  configuration:
    rules: {mcp/session-mcp.opted/opt_forbidden: ask}
"""
    )
    script = tmp_path / "server.py"
    script.write_text("""
import os
from pathlib import Path
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
Path(os.environ['MARKER']).write_text('launched')
mcp = FastMCP('test')
@mcp.tool
async def opt_echo(ctx: Context) -> str:
    try:
        result = await ctx.elicit('Do not ask a person', bool)
    except ToolError as error:
        # The modern MCP version has no server-initiated elicitation channel.
        if 'elicitation via server-initiated requests is unavailable' not in str(error):
            raise
    else:
        assert result.action == 'decline'
    return 'tool returned'
@mcp.tool
async def opt_forbidden() -> str:
    return 'must be filtered before exposure'
mcp.run(show_banner=False)
""")
    started = tmp_path / "opted-marker"
    skipped = tmp_path / "skipped-marker"

    def entry(name, marker):
        return {
            "name": name,
            "command": sys.executable,
            "args": [str(script)],
            "env": [{"name": "MARKER", "value": str(marker)}, {"name": "SECRET", "value": "transient-token"}],
        }

    entries = [entry("opted", started), entry("skipped", skipped)]
    async with _agent(path, tmp_path, unattended=True) as rpc:
        await rpc.send(
            "session/new",
            id=2,
            cwd=str(tmp_path),
            mcpServers=entries,
            _meta={PROFILE: {"version": 1, "unattendedSafeMcpServers": ["unknown"]}},
        )
        reply, _ = await rpc.response(2)
        assert reply["error"]["code"] == -32602
        await rpc.send(
            "session/new",
            id=99,
            cwd=str(tmp_path),
            mcpServers=entries,
            _meta={PROFILE: {"version": 1, "unattendedSafeMcpServers": ["opted", "opted"]}},
        )
        reply, _ = await rpc.response(99)
        assert reply["error"]["code"] == -32602
        await rpc.send(
            "session/new",
            id=3,
            cwd=str(tmp_path),
            mcpServers=entries,
            _meta={PROFILE: {"version": 1, "unattendedSafeMcpServers": ["opted"]}},
        )
        reply, _ = await rpc.response(3)
        assert reply["result"]["_meta"][PROFILE]["omittedMcpServers"] == [
            {"name": "skipped", "reason": "unattended_safety_not_declared"}
        ]
        session_id = reply["result"]["sessionId"]
        await rpc.send("_a13n/inventory", id=5, sessionId=session_id)
        inventory, _ = await rpc.response(5)
        assert inventory["result"]["mcpServers"] == [{"name": "opted", "source": "session"}]
        assert inventory["result"]["omittedMcpServers"] == [
            {"name": "skipped", "reason": "unattended_safety_not_declared"}
        ]
        await rpc.send("session/prompt", id=4, sessionId=session_id, prompt=[{"type": "text", "text": "show tools"}])
        reply, frames = await rpc.response(4)
        assert reply.get("result", {}).get("stopReason") == "end_turn", reply
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "MCP worked"
        updates = [frame["params"]["update"] for frame in frames if frame.get("method") == "session/update"]
        call = next(update for update in updates if update.get("title") == "opt_echo")
        assert any(
            update.get("toolCallId") == call["toolCallId"] and update.get("status") == "completed" for update in updates
        )
        assert started.exists() and not skipped.exists()
    assert "unattended_safety_not_declared" in rpc.diagnostics and "skipped" in rpc.diagnostics
    started.unlink()
    async with _agent(path, tmp_path, unattended=True) as rpc:
        await rpc.send("session/resume", id=2, sessionId=session_id, cwd=str(tmp_path), mcpServers=entries)
        reply, _ = await rpc.response(2)
        assert reply["result"]["_meta"][PROFILE]["omittedMcpServers"] == [
            {"name": entry["name"], "reason": "unattended_safety_not_declared"} for entry in entries
        ]
        await rpc.send("_a13n/inventory", id=4, sessionId=session_id)
        inventory, _ = await rpc.response(4)
        assert inventory["result"]["mcpServers"] == []
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "show tools"}])
        reply, frames = await rpc.response(3)
        assert reply.get("result", {}).get("stopReason") == "end_turn", reply
        assert not any(frame.get("params", {}).get("update", {}).get("title") == "opt_echo" for frame in frames)
    assert not started.exists() and not skipped.exists()
    assert all(b"transient-token" not in file.read_bytes() for file in (tmp_path / "data").rglob("*") if file.is_file())


@pytest.mark.anyio
async def test_harness_acp_client_interoperates_with_ui_agent(tmp_path, monkeypatch):
    from a13n_harness.acp import AcpRunBinding, AcpSessionRuntime, AcpSessionSetup, LocalStdioTransport
    from acp.schema import AgentMessageChunk, TextContentBlock

    path = await _seed(tmp_path, monkeypatch)
    transport = LocalStdioTransport(
        sys.executable,
        str(tmp_path),
        ("-c", _FAKE, "--config", str(path), "--data-root", str(tmp_path / "data"), "acp"),
        inherit_environment=True,
    )
    setup = AcpSessionSetup(str(tmp_path))
    async with asyncio.timeout(45):
        async with AcpSessionRuntime.open(transport) as runtime:
            session_id = await runtime.open_session(setup)
            updates = []
            response = await runtime.prompt(
                [TextContentBlock(text="model check")], binding=AcpRunBinding(on_update=updates.append)
            )
            assert response.stop_reason == "end_turn"
            assert "gpt-5.6-sol high" in "".join(
                update.content.text
                for update in updates
                if isinstance(update, AgentMessageChunk) and isinstance(update.content, TextContentBlock)
            )
        async with AcpSessionRuntime.open(transport) as runtime:
            assert await runtime.open_session(setup, saved_session_id=session_id) == session_id
            response = await runtime.prompt([TextContentBlock(text="model check")], binding=AcpRunBinding())
            assert response.stop_reason == "end_turn"
