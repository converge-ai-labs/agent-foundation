"""ACP permissions, unattended execution, and interactive tool tests."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
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

from .acp_test_support import _FAKE, PROFILE, _agent, _wait_for_marker
from .test_interactive import _seed


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


@pytest.mark.xdist_group("harness-ui-acp-approval-and-cancel")
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


@pytest.mark.xdist_group("harness-ui-acp-checkpoint-disconnect")
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


@pytest.mark.parametrize(
    "acp",
    [
        pytest.param(False, marks=pytest.mark.xdist_group("harness-ui-acp-unattended-review-oneshot")),
        pytest.param(True, marks=pytest.mark.xdist_group("harness-ui-acp-unattended-review-acp")),
    ],
)
@pytest.mark.anyio
async def test_default_unattended_surface_and_shell_review(tmp_path, monkeypatch, acp):
    path = await _seed(tmp_path, monkeypatch, shell_review=True)
    root = yaml.safe_load(path.read_text())
    root["agent_creation"] = {"allowed_models": ["model-codex"], "default_model": "model-codex"}
    root["subagents"] = {"dynamic": {"enabled": True}}
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


@pytest.mark.xdist_group("harness-ui-acp-unsupported-input")
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


@pytest.mark.xdist_group("harness-ui-acp-ephemeral-mcp")
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
