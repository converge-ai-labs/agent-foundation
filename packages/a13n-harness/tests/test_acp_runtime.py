from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from a13n_harness.acp import (
    AcpError,
    AcpHostToolBridge,
    AcpRunBinding,
    AcpSessionRuntime,
    AcpSessionSetup,
    LocalStdioTransport,
)
from a13n_harness.context import AgentContext
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from acp import schema
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from .environment_helpers import Source
from .test_environment_connectors import bind

pytestmark = pytest.mark.anyio
_FIXTURE = Path(__file__).parent / "fixtures" / "acp_peer.py"


def local(path, mode, *flags):
    return LocalStdioTransport(sys.executable, str(path), (str(_FIXTURE), mode, *flags), inherit_environment=True)


def setup(path):
    return AcpSessionSetup(str(path), instructions=("guide",))


def source(path):
    return Source(
        DIRECT_LOCAL.execution_connector({"root": {"path": str(path)}, "allowed_executables": [sys.executable]})
    )


async def test_environment_run_callbacks(tmp_path):
    callbacks = create_environment_runtime(mounts={"workspace": source(tmp_path)}, default_mount="workspace")
    reviewed = []

    async def review(request):
        reviewed.append(request.command.executable)
        return request.command.executable != "denied"

    async with bind(callbacks, "turn") as turn, asyncio.timeout(10):
        transport = local(tmp_path, "callbacks")
        async with AcpSessionRuntime.open(transport) as acp:
            await acp.open_session(setup(tmp_path))
            starts = []
            response = await acp.prompt(
                [schema.TextContentBlock(text="callbacks")],
                binding=AcpRunBinding(
                    environment=turn,
                    review_command=review,
                    on_update=starts.append,
                ),
            )
            assert response.stop_reason == "end_turn"
            assert [u.content.text for u in starts if isinstance(u, schema.AgentMessageChunk)] == ["done:callbacks"]
            assert Path(reviewed[0]).resolve() == Path(sys.executable).resolve()
            assert reviewed[1:] == ["denied"]


@pytest.mark.parametrize("mode", ["load", "resume", "standard-steer"])
async def test_recovery_permission_steer_and_cancel(tmp_path, mode):
    requested = asyncio.Event()
    ended = asyncio.Event()
    updates = []

    async def permission(_, __):
        requested.set()
        try:
            await asyncio.Future()
        finally:
            ended.set()

    async with asyncio.timeout(10), AcpSessionRuntime.open(local(tmp_path, mode)) as acp:
        assert await acp.open_session(setup(tmp_path), saved_session_id="fixture-session") == "fixture-session"
        first = asyncio.create_task(
            acp.prompt(
                [schema.TextContentBlock(text="wait")],
                binding=AcpRunBinding(
                    permission=permission,
                    on_update=updates.append,
                ),
            )
        )
        await requested.wait()
        assert await acp.steer("interrupt") == ("unsupported" if mode == "load" else "accepted")
        if mode == "standard-steer":
            assert await acp.steer("next turn") == "not_delivered"
        assert await acp.cancel()
        assert (await first).stop_reason == "cancelled"
        assert ended.is_set()
        await acp.prompt([schema.TextContentBlock(text="again")], binding=AcpRunBinding(on_update=updates.append))
        assert [u.content.text for u in updates if isinstance(u, schema.AgentMessageChunk)] == ["done:again"]


@pytest.mark.parametrize("flag", ["null-model", "missing-model"])
@pytest.mark.parametrize("mode", ["ui", "load", "resume", "legacy"])
async def test_unselected_models_survive_session_setup(tmp_path, mode, flag):
    saved = "fixture-session" if mode in {"load", "resume"} else None
    async with asyncio.timeout(10), AcpSessionRuntime.open(local(tmp_path, mode, flag)) as acp:
        await acp.open_session(setup(tmp_path), saved_session_id=saved)
        (option,) = [item for item in acp.config_options if item.id == "model"]
        assert isinstance(option, schema.SessionConfigOptionSelect)
        assert option.current_value == ""
        assert [choice.value for choice in option.options] == ["a", "b"]
        with pytest.raises(AcpError, match="could not confirm"):
            await acp.restore_options({"model": ""}, strict=frozenset({"model"}))
        if mode in {"ui", "legacy"}:
            await acp.restore_options({"model": "b"}, strict=frozenset({"model"}))
            assert next(item for item in acp.config_options if item.id == "model").current_value == "b"


@pytest.mark.parametrize("flag", ["null-model", "missing-model"])
async def test_unselected_models_survive_async_config_update(tmp_path, flag):
    async with asyncio.timeout(10), AcpSessionRuntime.open(local(tmp_path, "update", flag)) as acp:
        await acp.open_session(setup(tmp_path))
        assert not acp.config_options
        await acp.connection.ext_method("fixture/announce_options", {})
        await acp.wait_for_model_option(1)
        assert [(item.id, item.current_value) for item in acp.config_options] == [("mode", "chat"), ("model", "")]


async def test_vendor_system_prompt_and_legacy_model_selection(tmp_path):
    async with asyncio.timeout(10), AcpSessionRuntime.open(local(tmp_path, "legacy")) as acp:
        await acp.open_session(setup(tmp_path))
        assert [(option.id, option.current_value) for option in acp.config_options] == [("model", "a")]
        await acp.restore_options({"model": "b"})
        assert [(option.id, option.current_value) for option in acp.config_options] == [("model", "b")]


@pytest.mark.parametrize(
    ("mode", "recover"), [("remote", False), ("http", False), ("stdio", False), ("http", True), ("load", True)]
)
async def test_shared_dispatch_and_call_correlation(tmp_path, mode, recover):
    toolset: FunctionToolset[AgentContext] = FunctionToolset()

    @toolset.tool
    async def identify(ctx: RunContext[AgentContext]) -> str:
        assert ctx.deps.thread_id == "thread-owner"
        assert ctx.tool_call_id is not None
        return ctx.tool_call_id

    bridge = AcpHostToolBridge([], toolsets=[toolset])
    deps = cast(AgentContext, SimpleNamespace(run_id="run-owner", thread_id="thread-owner"))
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    async with (
        asyncio.timeout(12),
        AcpSessionRuntime.open(
            local(tmp_path, mode),
            host_tools=bridge,
            same_network=mode != "remote",
        ) as acp,
        bridge.prepare(ctx) as scope,
    ):
        await acp.open_session(
            setup(tmp_path), discovery=scope, saved_session_id="fixture-session" if recover else None
        )
        await acp.connection.ext_method("fixture/discover", {})
        updates = []
        await acp.prompt(
            [schema.TextContentBlock(text="tools")], binding=AcpRunBinding(host_tools=scope, on_update=updates.append)
        )
        assert [u.content.text for u in updates if isinstance(u, schema.AgentMessageChunk)] == [
            "one",
            "exact",
            "explicit" if mode == "remote" else "run-owner",
            "run-owner",
            "done:tools",
        ]


async def test_first_prompt_fallback_is_sent_once_on_new_session(tmp_path):
    async with asyncio.timeout(8), AcpSessionRuntime.open(local(tmp_path, "new-no-system")) as acp:
        await acp.open_session(setup(tmp_path))
        for message in ("first", "again"):
            result = await acp.prompt([schema.TextContentBlock(text=message)], binding=AcpRunBinding())
            assert result.stop_reason == "end_turn"


async def test_remote_without_advertised_bridge_fails_early(tmp_path):
    with pytest.raises(AcpError, match="MCP-over-ACP"):
        async with (
            asyncio.timeout(6),
            AcpSessionRuntime.open(local(tmp_path, "http"), host_tools=AcpHostToolBridge([]), same_network=False),
        ):
            pytest.fail("remote loopback fallback")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group probe")
async def test_abandoned_agent_descendant_stops(tmp_path):
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); print('{}',flush=True); time.sleep(30)"
    marker = tmp_path / "survived"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
    transport = LocalStdioTransport(sys.executable, str(tmp_path), ("-c", code, child))
    async with asyncio.timeout(6), transport.open() as channel:
        assert await channel.receive() == {}
    await asyncio.sleep(1.1)
    assert not marker.exists()


async def test_native_stdio_reads_past_command_observation_limit(tmp_path):
    script = "import json,sys; print(json.dumps({'payload': 'x' * 150000}), flush=True); sys.stdin.read()"
    transport = LocalStdioTransport(sys.executable, str(tmp_path), ("-c", script))
    async with asyncio.timeout(6), transport.open() as channel:
        assert await channel.receive() == {"payload": "x" * 150000}


async def test_terminal_release_joins_tree_cleanup_when_turn_is_revoked():
    from a13n_harness.acp import AcpEnvironmentClient
    from a13n_harness.acp.client import _Terminal

    waiting = asyncio.Event()
    proceed = asyncio.Event()
    calls = []

    async def close_stdin(_):
        pass

    async def wait(_, **__):
        waiting.set()
        await proceed.wait()
        return SimpleNamespace(status=SimpleNamespace(cleanup="complete"))

    async def inspect(_):
        return SimpleNamespace(status=SimpleNamespace(phase="running", cleanup="pending"))

    async def kill(_):
        calls.append("kill")

    async def release(_):
        calls.append("release")

    processes = SimpleNamespace(close_stdin=close_stdin, wait=wait, inspect=inspect, kill=kill, release=release)
    client = AcpEnvironmentClient()
    client.session_id = "session"
    binding = AcpRunBinding(environment=SimpleNamespace(processes=processes))
    client.binding = binding
    client._terminals["terminal"] = _Terminal(object(), 100)
    async with asyncio.timeout(6):
        callback = asyncio.create_task(client.release_terminal("session", "terminal"))
        await waiting.wait()
        unbind = asyncio.create_task(client._unbind(binding))
        await asyncio.sleep(0)
        assert not unbind.done()
        assert "terminal" in client._terminals
        proceed.set()
        await unbind
        assert callback.cancelled()
        assert calls == ["kill", "release"]
        assert not client._terminals
