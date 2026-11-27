"""ACP stdio transport, flow control, and extension negotiation tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import ExitStack
from types import SimpleNamespace
from typing import cast

import pytest
from a13n_harness_ui.acp_server import AcpAgent, _Turn
from a13n_harness_ui.app import HarnessUiApp
from acp import Client
from acp.schema import TextContentBlock

from .acp_test_support import PROFILE, _agent, _wait_for_marker
from .test_interactive import _seed


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


@pytest.mark.anyio
async def test_acp_server_forwards_compaction_only_when_advertised() -> None:
    from a13n_stream_protocol.fragments import fragment_custom_event
    from acp.schema import (
        ClientCapabilities,
        ClientSessionCapabilities,
        CompactionCapabilities,
        SessionUpdateCompactionUpdate,
    )
    from ag_ui.core.events import CustomEvent

    sent: list[tuple[str, object]] = []

    class FakeClient:
        async def session_update(self, session_id: str, update: object, **_: object) -> None:
            sent.append((session_id, update))

    agent = AcpAgent(cast(HarnessUiApp, SimpleNamespace()))
    agent.on_connect(cast(Client, FakeClient()))

    # 1. Client does not advertise session.compaction -> never send compaction updates.
    await agent.initialize(1, client_capabilities=ClientCapabilities())
    assert not agent.client_supports_compaction
    turn = _Turn(turn_id="t-1")
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.harness.context",
            "value": {"event": {"payload": {"type": "compaction_started", "operation_id": "compaction-1"}}},
        },
    )
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.context.compaction_summary",
            "value": {"event": {"operation_id": "compaction-1", "summary": "Should not send"}},
        },
    )
    assert sent == []

    # 2. Dict client_capabilities advertising session.compaction works as well.
    await agent.initialize(1, client_capabilities={"session": {"compaction": {}}})
    assert agent.client_supports_compaction

    # 3. Typed ClientCapabilities advertising session.compaction forwards lifecycle & summaries in both orderings.
    await agent.initialize(
        1,
        client_capabilities=ClientCapabilities(session=ClientSessionCapabilities(compaction=CompactionCapabilities())),
    )
    assert agent.client_supports_compaction
    turn = _Turn(turn_id="t-2")

    # Ordering A: started -> completed -> summary (inline compaction).
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.harness.context",
            "value": {"event": {"payload": {"type": "compaction_started", "operation_id": "compaction-a"}}},
        },
    )
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.harness.context",
            "value": {"event": {"payload": {"type": "compaction_completed", "operation_id": "compaction-a"}}},
        },
    )
    # Fragmented summary event reassembles and preserves completed status.
    large_summary = "Summary text " * 5000
    fragments = fragment_custom_event(
        CustomEvent(
            name="a13n.context.compaction_summary",
            value={"event": {"operation_id": "compaction-a", "summary": large_summary}},
        ),
        identity="frag-a",
    )
    assert len(fragments) > 1
    for fragment in fragments:
        await agent._custom_update("s-1", turn, fragment.model_dump(mode="json", by_alias=True))

    # Ordering B: summary -> completed (background/restored compaction).
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.context.compaction_summary",
            "value": {"event": {"operation_id": "compaction-b", "summary": "Restored summary"}},
        },
    )
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.harness.context",
            "value": {"event": {"payload": {"type": "compaction_completed", "operation_id": "compaction-b"}}},
        },
    )

    # Failure maps error_code/reason into error.
    await agent._custom_update(
        "s-1",
        turn,
        {
            "type": "CUSTOM",
            "name": "a13n.harness.context",
            "value": {
                "event": {
                    "payload": {
                        "type": "compaction_failed",
                        "operation_id": "compaction-c",
                        "error_code": "compaction_summary_invalid",
                        "reason": "empty summary",
                    }
                }
            },
        },
    )

    updates = [update for _, update in sent]
    assert all(isinstance(update, SessionUpdateCompactionUpdate) for update in updates)
    assert [(u.compaction_id, u.status) for u in updates] == [
        ("compaction-a", "in_progress"),
        ("compaction-a", "completed"),
        ("compaction-a", "completed"),
        ("compaction-b", "in_progress"),
        ("compaction-b", "completed"),
        ("compaction-c", "failed"),
    ]
    assert updates[2].summary == [TextContentBlock(type="text", text=large_summary)]
    assert updates[3].summary == [TextContentBlock(type="text", text="Restored summary")]
    assert updates[4].summary is None
    assert updates[5].error == "compaction_summary_invalid: empty summary"
