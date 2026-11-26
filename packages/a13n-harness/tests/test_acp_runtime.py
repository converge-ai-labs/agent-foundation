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
from pydantic_ai import BinaryContent, FunctionToolset, RunContext, TextContent, ToolReturn
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


async def test_host_tool_bridge_preserves_tool_return_binary_and_text_content(tmp_path):
    import json
    from base64 import b64encode

    jpeg = b"\xff\xd8\xff\xe0fixture-jpeg"
    audio = b"RIFFfixture-audio"
    toolset: FunctionToolset[AgentContext] = FunctionToolset()

    @toolset.tool_plain
    async def screenshot() -> ToolReturn:
        return ToolReturn(
            return_value={"ref": "s1", "width": 640},
            content=[
                BinaryContent(data=jpeg, media_type="image/jpeg"),
                BinaryContent(data=audio, media_type="audio/wav"),
                TextContent("captured viewport"),
                "plain note",
            ],
        )

    bridge = AcpHostToolBridge([], toolsets=[toolset])
    deps = cast(AgentContext, SimpleNamespace(run_id="run-owner", thread_id="thread-owner"))
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    async with (
        asyncio.timeout(12),
        AcpSessionRuntime.open(local(tmp_path, "ui", "use-http"), host_tools=bridge) as acp,
        bridge.prepare(ctx) as scope,
    ):
        await acp.open_session(setup(tmp_path), discovery=scope)
        updates = []
        await acp.prompt(
            [schema.TextContentBlock(text="call-screenshot")],
            binding=AcpRunBinding(host_tools=scope, on_update=updates.append),
        )
        reply = "".join(u.content.text for u in updates if isinstance(u, schema.AgentMessageChunk))
        payload = json.loads(reply)
        assert payload["isError"] is False
        assert payload["content"] == [
            {"type": "text", "text": json.dumps({"ref": "s1", "width": 640})},
            {"type": "image", "data": b64encode(jpeg).decode("ascii"), "mimeType": "image/jpeg"},
            {"type": "audio", "data": b64encode(audio).decode("ascii"), "mimeType": "audio/wav"},
            {"type": "text", "text": "captured viewport"},
            {"type": "text", "text": "plain note"},
        ]


async def test_multimodal_session_steering_and_prompt_block_separation(tmp_path):
    import json
    from base64 import b64encode

    png = b"\x89PNG\r\n\x1a\nfixture"
    updates = []
    ready = asyncio.Event()

    def observe(update):
        updates.append(update)
        if isinstance(update, schema.AgentMessageChunk) and update.content.text == "waiting":
            ready.set()

    async with asyncio.timeout(10), AcpSessionRuntime.open(local(tmp_path, "ui", "standard-steer")) as acp:
        await acp.open_session(setup(tmp_path))
        assert acp.supports_multimodal_steering
        first = asyncio.create_task(
            acp.prompt(
                [schema.TextContentBlock(text="wait-steer")],
                binding=AcpRunBinding(on_update=observe),
            )
        )
        await ready.wait()
        outcome = await acp.steer(
            [
                schema.TextContentBlock(text="Inspect image"),
                schema.ImageContentBlock(data=b64encode(png).decode("ascii"), mime_type="image/png"),
            ]
        )
        assert outcome == "accepted"
        assert (await first).stop_reason == "end_turn"
        steer_payload = json.loads(
            "".join(
                u.content.text
                for u in updates
                if isinstance(u, schema.AgentMessageChunk) and u.content.text != "waiting"
            )
        )
        assert steer_payload == [
            {"type": "text", "text": "Inspect image"},
            {"type": "image", "data": b64encode(png).decode("ascii"), "mimeType": "image/png"},
        ]

        updates.clear()
        await acp.prompt(
            [schema.TextContentBlock(text="prefix"), schema.TextContentBlock(text="echo-prompt")],
            binding=AcpRunBinding(on_update=updates.append),
            context=("ctx-1", "ctx-2"),
        )
        prompt_payload = json.loads("".join(u.content.text for u in updates if isinstance(u, schema.AgentMessageChunk)))
        assert prompt_payload == [
            {"type": "text", "text": "ctx-1\n\nctx-2\n\n"},
            {"type": "text", "text": "prefix\n\n"},
            {"type": "text", "text": "echo-prompt"},
        ]


async def test_stdio_mcp_forwarder_uses_isolated_script_without_package_import():
    import json
    import subprocess

    from a13n_harness.acp import _stdio

    toolset: FunctionToolset[AgentContext] = FunctionToolset()

    @toolset.tool_plain
    async def echo(value: str) -> str:
        return f"echo:{value}"

    bridge = AcpHostToolBridge([], toolsets=[toolset])
    deps = cast(AgentContext, SimpleNamespace(run_id="run-owner", thread_id="thread-owner"))
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    expected_script = str(Path(_stdio.__file__))

    async with asyncio.timeout(12), bridge._connection(), bridge.prepare(ctx) as scope:
        await bridge._discover(scope)
        bridge._bind(scope, "turn-1")
        try:
            async with bridge._loopback(http=False) as server:
                assert isinstance(server, schema.McpServerStdio)
                assert server.command == sys.executable
                assert server.args == ["-I", expected_script]

                env = {**os.environ, **{var.name: var.value for var in server.env}}
                proc = await asyncio.create_subprocess_exec(
                    server.command,
                    *server.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

                async def rpc(payload: dict[str, object]) -> dict[str, object]:
                    assert proc.stdin is not None and proc.stdout is not None
                    proc.stdin.write((json.dumps(payload) + "\n").encode())
                    await proc.stdin.drain()
                    return json.loads(await proc.stdout.readline())

                init = await rpc(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
                )
                assert init["result"]["capabilities"] == {"tools": {}}

                proc.stdin.write(
                    (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
                )
                await proc.stdin.drain()

                listed = await rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                assert [tool["name"] for tool in listed["result"]["tools"]] == ["echo"]

                called = await rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "fast"}},
                    }
                )
                assert called["result"] == {"content": [{"type": "text", "text": "echo:fast"}], "isError": False}

                proc.stdin.close()
                await proc.stdin.wait_closed()
                assert await proc.wait() == 0
                assert await proc.stderr.read() == b""

                # Verify isolated direct execution does not import a13n_harness or heavy runtime packages.
                probe_code = (
                    "import json, runpy, sys; "
                    f"runpy.run_path({expected_script!r}, run_name='__main__'); "
                    "loaded = sorted(m for m in sys.modules if m.startswith(('a13n_harness', 'pydantic', 'aiohttp'))); "
                    "print(json.dumps(loaded))"
                )
                probe = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-I", "-c", probe_code],
                    input="",
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                assert json.loads(probe.stdout) == []

                # Verify -m invocation remains compatible.
                module_proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "a13n_harness.acp._stdio",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                assert (
                    module_proc.stdin is not None and module_proc.stdout is not None and module_proc.stderr is not None
                )
                module_proc.stdin.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "initialize",
                                "params": {"protocolVersion": "2025-11-25"},
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await module_proc.stdin.drain()
                module_init = json.loads(await module_proc.stdout.readline())
                assert module_init["result"]["capabilities"] == {"tools": {}}
                module_proc.stdin.close()
                await module_proc.stdin.wait_closed()
                assert await module_proc.wait() == 0
                assert await module_proc.stderr.read() == b""
        finally:
            await bridge._unbind()
