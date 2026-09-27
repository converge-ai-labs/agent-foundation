from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpCommandTransport
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from a13n_harness_ui.mcp_apps.connections import MIME_TYPE, AppToolset, Connections
from fastmcp.client.transports import StdioTransport
from fastmcp.tools import ToolResult
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio

_SERVER = """
import asyncio
import os
from fastmcp import FastMCP, Context
from fastmcp.tools import ToolResult
from mcp.types import TextContent
server = FastMCP("Apps fixture")
count = 0
@server.tool(meta={"ui": {"resourceUri": "ui://counter/app.html"}})
async def counter(ctx: Context, delay: float = 0) -> ToolResult:
    global count
    await asyncio.sleep(delay)
    count += 1
    return ToolResult(
        content=[TextContent(type="text", text=f"count={count}")],
        structured_content={"count": count, "pid": os.getpid(), "capabilities": ctx.session.client_params.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)},
        meta={"private": "not-for-the-model"},
    )
@server.tool(meta={"ui": {"visibility": ["app"]}})
def reset() -> str:
    global count
    count = 0
    return "reset"
@server.resource("ui://counter/app.html", mime_type="text/html;profile=mcp-app")
def app() -> str:
    return "<!doctype html><p>Counter</p>"
@server.resource("data://counter/{name}")
def item(name: str) -> str:
    return f"item={name};count={count}"
server.run(transport="stdio", show_banner=False)
"""


def _server(tmp_path: Path) -> StdioTransport:
    script = tmp_path / "apps_server.py"
    script.write_text(_SERVER)
    return StdioTransport(command=sys.executable, args=[str(script)], keep_alive=False)


async def test_stdio_connection_outlives_toolsets_and_explicit_close_stops_it(tmp_path: Path) -> None:
    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "mcp-counter", "recipe", _server(tmp_path))
        async with AppToolset(connection, connections.captures) as first:
            assert {tool.name for tool in await first.list_tools()} == {"counter"}
            initial = await first.direct_call_tool("counter", {})
        assert connection.connected
        async with AppToolset(connection, connections.captures) as second:
            later = await second.direct_call_tool("counter", {})
        assert initial["count"] == 1
        assert later["count"] == 2
        assert connection.client.initialize_result is not None
        assert initial["capabilities"].get("extensions", {}).get("io.modelcontextprotocol/ui") == {
            "mimeTypes": [MIME_TYPE]
        }
        assert not any(initial["capabilities"].get(key) for key in ("sampling", "elicitation", "roots", "tasks"))
        resource = await connection.client.read_resource("ui://counter/app.html")
        assert resource[0].mime_type == MIME_TYPE
        # A browser can call the app-only tool on the exact retained session.
        assert (await connection.client.call_tool_mcp("reset", {})).content[0].type == "text"
        await connections.close_thread("thread-one")
        assert not connection.connected
        with pytest.raises(RuntimeError, match="explicit activation"):
            async with AppToolset(connection, connections.captures):
                pass
    finally:
        await connections.close()


async def test_http_session_preserves_original_capture_and_stays_usable_between_runs(tmp_path: Path) -> None:
    from fastmcp import FastMCP
    from fastmcp.client.transports import StreamableHttpTransport
    from mcp.types import TextContent

    from .test_tool_proxy_integration import _http_mcp

    server = FastMCP("HTTP Apps")
    count = 0

    @server.tool(meta={"ui": {"resourceUri": "ui://http/app.html"}})
    def counter() -> ToolResult:
        nonlocal count
        count += 1
        return ToolResult(
            content=[TextContent(type="text", text="HTTP counter")],
            structured_content={"count": count},
            meta={"private": "original"},
        )

    @server.resource("ui://http/app.html", mime_type=MIME_TYPE)
    def app() -> str:
        return "<!doctype html><p>HTTP counter</p>"

    async with _http_mcp(server) as url:
        connections = Connections()
        try:
            connection = await connections.acquire("thread-one", "counter", "recipe", StreamableHttpTransport(url))
            for expected in (1, 2):
                async with AppToolset(connection, connections.captures) as toolset:
                    assert (await toolset.direct_call_tool("counter", {}))["count"] == expected
                assert connection.connected
            assert connection.client.initialize_result is not None
            assert (await connection.client.call_tool_mcp("counter", {})).meta == {"private": "original"}
            assert (await connection.client.read_resource_mcp("ui://http/app.html")).contents[0].mime_type == MIME_TYPE
            assert count == 3
        finally:
            await connections.close()


async def test_cancelled_borrower_does_not_close_other_borrow_or_owner(tmp_path: Path) -> None:
    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "mcp-counter", "recipe", _server(tmp_path))
        entered = asyncio.Event()

        async def cancelled_run() -> None:
            async with AppToolset(connection, connections.captures) as toolset:
                entered.set()
                await toolset.direct_call_tool("counter", {"delay": 30})

        task = asyncio.create_task(cancelled_run())
        await entered.wait()
        async with AppToolset(connection, connections.captures) as survivor:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert connection.connected
            assert (await survivor.direct_call_tool("counter", {}))["count"] == 1
        assert connection.connected
    finally:
        await connections.close()


async def test_real_agent_captures_complete_result_without_changing_model_output(tmp_path: Path) -> None:
    transport = _server(tmp_path)
    connections = Connections()
    recipe = ResolvedMcpRecipe(
        server_id="mcp-counter",
        transport=McpCommandTransport(command=sys.executable, arguments=tuple(transport.args)),
    )

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            assert returns[-1].content["count"] == 1
            assert "not-for-the-model" not in str(returns[-1].content)
            yield "done"
        else:
            assert not any("reset" in tool.name for tool in info.function_tools)
            tool = next(tool for tool in info.function_tools if "counter" in tool.name)
            yield {0: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id="counter-call")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(HarnessUiMCP(recipe, apps=connections),),
    )
    try:
        result = await executable.run("Show the counter", bindings=RunBindings.embedded())
        assert result.status == "completed"
        captures = connections.captures.take(result.run_id, "counter-call")
        assert len(captures) == 1
        assert captures[0].result.meta == {"private": "not-for-the-model"}
        assert captures[0].result.structured_content["count"] == 1
        assert captures[0].connection.connected
        later = await captures[0].connection.client.call_tool_mcp("counter", {})
        assert later.structured_content["count"] == 2
        assert later.structured_content["pid"] == captures[0].result.structured_content["pid"]
    finally:
        await connections.close()


@pytest.mark.parametrize("missing_resource", [False, True])
async def test_original_snapshot_is_retained_without_replaying_tools(tmp_path: Path, missing_resource: bool) -> None:
    from a13n_harness_ui.mcp_apps.connections import CapturedCall
    from a13n_harness_ui.mcp_apps.models import AppResource, AppSnapshot
    from a13n_harness_ui.mcp_apps.snapshots import METADATA_KEY, AppSnapshots, app_references
    from a13n_harness_ui.settings import StorageSettings
    from a13n_harness_ui.storage import open_local_store
    from a13n_harness_ui.storage.contracts import CompactChildDisplay
    from a13n_harness_ui.subagent_operator import _DisplayCompactor
    from ag_ui.core import CustomEvent

    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "mcp-counter", "recipe", _server(tmp_path))
        tool = next(item for item in await connection.client.list_tools() if item.name == "counter")
        if missing_resource:
            tool = tool.model_copy(update={"meta": {"ui": {"resourceUri": "ui://missing/app.html"}}})
        raw = await connection.client.call_tool_mcp("counter", {})
        async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
            snapshots = AppSnapshots(store.objects, connections)
            reference = await snapshots.capture(
                CapturedCall(connection, tool, {}, raw), thread_id="thread-one", run_id="run-one", call_id="call-one"
            )
            assert reference.snapshot is not None
            metadata = {METADATA_KEY: [reference.model_dump(mode="json")]}
            part = ToolReturnPart("counter", {"count": 1}, tool_call_id="call-one", metadata=metadata)
            assert app_references(part) == (reference,)
            compactor = _DisplayCompactor(CompactChildDisplay())
            compactor.observe((CustomEvent(name=METADATA_KEY, value={"event": {"apps": metadata[METADATA_KEY]}}),))
            assert compactor.snapshot().mcp_apps == (reference,)
            assert (await snapshots.read(reference)).connected
            saved = await store.objects.read_model(reference.snapshot, AppSnapshot)
            assert saved.result["_meta"] == {"private": "not-for-the-model"}
            assert saved.result["structuredContent"]["count"] == 1
            assert bool(saved.unavailable) is missing_resource
            if not missing_resource:
                assert saved.resource is not None
                resource = await store.objects.read_model(saved.resource, AppResource)
                assert resource.html == "<!doctype html><p>Counter</p>"
        await connections.close()
        async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
            reopened = await store.objects.read_model(reference.snapshot, AppSnapshot)
            assert reopened == saved
            presentation = await AppSnapshots(store.objects, Connections()).read(reference)
            assert not presentation.connected
            assert presentation.snapshot == saved
            assert bool(presentation.resource) is not missing_resource
    finally:
        await connections.close()


@pytest.mark.parametrize("codeact", [False, True])
async def test_nested_mcp_calls_attach_to_the_retained_outer_return(tmp_path: Path, codeact: bool) -> None:
    import json
    from dataclasses import dataclass

    from a13n_harness.capabilities import CodeActCapability, ToolProxyCapability, ToolProxyGroup
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic_ai.capabilities import WrapperCapability

    @dataclass
    class Eligible(WrapperCapability):
        def get_toolset(self):
            source = self.wrapped.get_toolset()
            return None if source is None else CodeActPolicyToolset(source, CodeActToolPolicy(tools={"counter": True}))

    transport = _server(tmp_path)
    connections = Connections()
    recipe = ResolvedMcpRecipe(
        server_id="counter",
        transport=McpCommandTransport(command=sys.executable, arguments=tuple(transport.args)),
    )
    returned = []

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            returned.extend(returns)
            yield "done"
        else:
            args = (
                {"code": "[await call_proxy_tool(group='apps', tool='counter', arguments={}) for _ in range(2)]"}
                if codeact
                else {"group": "apps", "tool": "counter", "arguments": {}}
            )
            yield {
                0: DeltaToolCall(
                    name="run_code" if codeact else "call_proxy_tool",
                    json_args=json.dumps(args),
                    tool_call_id="outer-call",
                )
            }

    capabilities = [
        ToolProxyCapability(groups={"apps": ToolProxyGroup(Eligible(HarnessUiMCP(recipe, apps=connections)), "Apps")})
    ]
    if codeact:
        capabilities.append(CodeActCapability())
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=capabilities
    )
    try:
        result = await executable.run("Show the counter", bindings=RunBindings.embedded())
        assert result.status == "completed"
        assert len(returned) == 1 and returned[0].tool_call_id == "outer-call"
        calls = connections.captures.take(result.run_id, returned[0].tool_call_id)
        assert len(calls) == (2 if codeact else 1)
        assert len({call.app_id for call in calls}) == len(calls)
        assert [call.result.structured_content["count"] for call in calls] == list(range(1, len(calls) + 1))
        assert not connections.captures._pending
    finally:
        await connections.close()


@pytest.mark.parametrize("in_flight", [False, True])
async def test_stdio_eof_retires_generation_without_reconnecting(tmp_path: Path, in_flight: bool) -> None:
    import os
    import signal

    from mcp.shared.exceptions import MCPError

    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))
        first = await connection.client.call_tool_mcp("counter", {})
        pending = None
        if in_flight:
            pending = asyncio.create_task(connection.client.call_tool_mcp("counter", {"delay": 30}))
            # Wait for dispatch admission, not for a timing-based guess at server completion.
            async with asyncio.timeout(5):
                while not connection.dispatch.locked():
                    await asyncio.sleep(0)
        os.kill(first.structured_content["pid"], signal.SIGTERM)
        async with asyncio.timeout(5):
            await connection._stop.wait()
        assert not connection.connected
        if pending is not None:
            with pytest.raises((MCPError, RuntimeError)):
                await pending
        with pytest.raises(RuntimeError, match="explicit activation"):
            await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))
        with pytest.raises(RuntimeError, match="explicit activation"):
            async with AppToolset(connection, connections.captures):
                pass
        with pytest.raises(RuntimeError, match="explicit activation"):
            await connection.client.call_tool_mcp("counter", {})
    finally:
        await connections.close()


async def test_business_error_does_not_disconnect_generation(tmp_path: Path) -> None:
    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))
        failed = await connection.client.call_tool_mcp("counter", {"delay": "invalid"})
        assert failed.is_error
        assert connection.connected
        assert (await connection.client.call_tool_mcp("counter", {})).structured_content["count"] == 1
    finally:
        await connections.close()


def test_capture_budget_bounds_nested_calls_and_total_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from a13n_harness_ui.mcp_apps import connections as module
    from mcp.types import CallToolResult, TextContent, Tool

    monkeypatch.setattr(module, "_MAX_PENDING_CALLS", 3)
    monkeypatch.setattr(module, "_MAX_CAPTURE_BYTES", 1024)
    monkeypatch.setattr(module, "_MAX_PENDING_BYTES", 2048)
    captures = module.Captures()
    call = module.CapturedCall(
        Mock(), Tool(name="counter", input_schema={}), {}, CallToolResult(content=[TextContent(type="text", text="x")])
    )
    for _ in range(10):
        captures.add("run-one", "outer", call)
    assert captures._count <= 3
    assert len(captures.take("run-one", "outer")) <= 3
    assert captures._bytes == captures._count == 0
    monkeypatch.setattr(module, "_MAX_PENDING_CALLS", 100)
    medium = module.CapturedCall(
        Mock(), call.tool, {}, CallToolResult(content=[TextContent(type="text", text="x" * 600)])
    )
    for index in range(10):
        captures.add("run-one", str(index), medium)
        assert captures._bytes <= 2048
    assert captures._count < 10
    captures.discard_run("run-one")
    assert captures._bytes == captures._count == 0
    oversized = module.CapturedCall(
        Mock(), call.tool, {}, CallToolResult(content=[TextContent(type="text", text="x" * 2048)])
    )
    captures.add("run-one", "oversized", oversized)
    assert captures.take("run-one", "oversized") == []


@pytest.mark.parametrize("change", ["binding", "selection"])
async def test_configuration_retirement_rejects_new_borrowers_but_finishes_admitted_calls(
    tmp_path: Path, change: str
) -> None:
    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path), binding="old")
        async with AppToolset(connection, connections.captures) as admitted:
            if change == "binding":
                connections.retain_bindings({"counter": "new"})
            else:
                connections.retain_thread_servers("thread-one", set())
            assert not connection.connected
            with pytest.raises(RuntimeError, match="retired"):
                async with AppToolset(connection, connections.captures):
                    pass
            assert (await admitted.direct_call_tool("counter", {}))["count"] == 1
        async with asyncio.timeout(5):
            await connection.close()
    finally:
        await connections.close()


async def test_startup_eof_finishes_owner_and_reports_failure(tmp_path: Path) -> None:
    connections = Connections()
    transport = StdioTransport(command=sys.executable, args=["-c", "pass"], keep_alive=False)
    try:
        async with asyncio.timeout(5):
            with pytest.raises(Exception, match=r"[Cc]losed|[Cc]onnect"):
                await connections.acquire("thread-one", "empty", "recipe", transport)
        assert not connections.get("thread-one", "empty").connected
    finally:
        await connections.close()


async def test_recipe_retirement_preserves_admitted_borrower(tmp_path: Path) -> None:
    connections = Connections()
    try:
        old = await connections.acquire("thread-one", "mcp-counter", "old", _server(tmp_path))
        async with AppToolset(old, connections.captures) as admitted:
            first = await admitted.direct_call_tool("counter", {})
            new = await connections.acquire("thread-one", "mcp-counter", "new", _server(tmp_path))
            assert old.retired and not old.connected
            assert new.connected
            assert (await admitted.direct_call_tool("counter", {}))["count"] == 2
            current = await new.client.call_tool_mcp("counter", {})
            assert current.structured_content["count"] == 1
            assert current.structured_content["pid"] != first["pid"]
        await old.close()
    finally:
        await connections.close()


@pytest.mark.parametrize("session", [True, False])
async def test_http_404_only_retires_an_established_session(session: bool) -> None:
    import json

    import httpx2
    from fastmcp.client.transports import StreamableHttpTransport
    from mcp.shared.exceptions import MCPError

    invocations = 0
    missing = False

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal invocations
        if request.method != "POST":
            return httpx2.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if "id" not in message:
            return httpx2.Response(202)
        headers = {"mcp-session-id": "fixture-session"} if session else {}
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "HTTP lifecycle fixture", "version": "1"},
            }
        elif method == "tools/call":
            invocations += 1
            if missing:
                return httpx2.Response(404, text="Session no longer exists")
            result = {"content": [{"type": "text", "text": "ok"}]}
        else:
            result = {"tools": []}
        return httpx2.Response(200, headers=headers, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), **kwargs)

    connections = Connections()
    try:
        transport = StreamableHttpTransport("http://fixture.invalid/mcp", httpx_client_factory=factory)
        connection = await connections.acquire("thread-http", "counter", "recipe", transport)
        await connection.client.call_tool_mcp("counter", {})
        missing = True
        with pytest.raises(MCPError, match="Session terminated" if session else "Not Found"):
            await connection.client.call_tool_mcp("counter", {})
        assert connection.connected is not session
        assert invocations == 2  # No transparent reconnection or business replay.
        if session:
            with pytest.raises(RuntimeError, match="explicit activation"):
                await connection.client.call_tool_mcp("counter", {})
            assert invocations == 2
    finally:
        await connections.close()


async def test_explicit_activation_replaces_failed_generation_without_replaying_tools(tmp_path: Path) -> None:
    connections = Connections()
    try:
        original = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))
        await original.client.call_tool_mcp("counter", {})
        await original.close()
        with pytest.raises(RuntimeError, match="explicit activation"):
            await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))
        activated = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path), activate=True)
        assert activated.generation != original.generation
        assert (await activated.client.call_tool_mcp("counter", {})).structured_content["count"] == 1
        # Activating another View on a healthy binding keeps the actual session.
        same = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path), activate=True)
        assert same is activated
    finally:
        await connections.close()


async def test_app_authorization_runs_in_dispatch_lane_and_cannot_capture_as_agent_call(tmp_path: Path) -> None:
    connections = Connections()
    checks = 0
    try:
        connection = await connections.acquire("thread-one", "counter", "recipe", _server(tmp_path))

        async def authorize() -> None:
            nonlocal checks
            assert connection.dispatch.locked()
            checks += 1
            if checks == 1:
                raise ValueError("Current policy denies this call")

        with pytest.raises(ValueError, match="denies"):
            await connection.client.call_app_tool("counter", {}, authorize=authorize)
        result = await connection.client.call_app_tool("counter", {}, authorize=authorize)
        assert result.structured_content["count"] == 1
        assert not connections.captures._pending
    finally:
        await connections.close()


def test_required_task_declaration_is_rejected_even_without_native_metadata() -> None:
    from a13n_harness_ui.mcp_apps.connections import require_classic_tool
    from mcp.types import Tool

    required = Tool.model_validate({"name": "required", "inputSchema": {}, "execution": {"taskSupport": "required"}})
    with pytest.raises(ValueError, match="Task-based"):
        require_classic_tool(required)
    require_classic_tool(Tool(name="ordinary", input_schema={}))


async def test_slow_server_startup_does_not_block_other_servers_and_shares_one_generation(tmp_path: Path) -> None:
    from contextlib import asynccontextmanager

    from fastmcp.client.transports import ClientTransport

    entered = asyncio.Event()
    release = asyncio.Event()
    underlying = _server(tmp_path)

    class PausedTransport(ClientTransport):
        @asynccontextmanager
        async def connect_session(self, **kwargs):
            entered.set()
            await release.wait()
            async with underlying.connect_session(**kwargs) as session:
                yield session

    connections = Connections()
    slow = asyncio.create_task(connections.acquire("thread-one", "slow", "recipe", PausedTransport()))
    same = None
    try:
        await entered.wait()
        same = asyncio.create_task(connections.acquire("thread-one", "slow", "recipe", PausedTransport()))
        async with asyncio.timeout(10):
            other = await connections.acquire("thread-two", "fast", "recipe", _server(tmp_path))
        assert other.connected
        release.set()
        first, second = await asyncio.gather(slow, same)
        assert first is second
        assert first.generation != other.generation
    finally:
        release.set()
        await asyncio.gather(slow, *([same] if same is not None else []), return_exceptions=True)
        await connections.close()


async def test_retirement_drains_an_admitted_app_call_without_reauthorizing_later_calls() -> None:
    from fastmcp import FastMCP
    from fastmcp.client.transports import FastMCPTransport

    entered = asyncio.Event()
    release = asyncio.Event()
    server = FastMCP("App retirement")

    @server.tool()
    async def business() -> str:
        entered.set()
        await release.wait()
        return "completed"

    async def authorize() -> None:
        pass

    connections = Connections()
    pending = None
    try:
        connection = await connections.acquire(
            "thread-one", "server", "recipe", FastMCPTransport(server), binding="original"
        )
        pending = asyncio.create_task(connection.client.call_app_tool("business", {}, authorize=authorize))
        async with asyncio.timeout(5):
            await entered.wait()
        connections.retain_bindings({"server": "changed"})
        assert not connection.connected
        assert not connection._stop.is_set()
        release.set()
        result = await pending
        assert result.content[0].text == "completed"
        with pytest.raises(RuntimeError, match="explicit activation"):
            await connection.client.call_app_tool("business", {}, authorize=authorize)
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await connections.close()
