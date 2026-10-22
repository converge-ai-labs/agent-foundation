"""Host invariants at Pydantic AI's public MCP Apps boundaries."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness_ui.mcp_apps.connections import MIME_TYPE, AppMCP, AppToolset, Captures, Connections
from a13n_harness_ui.mcp_apps.snapshots import read_app_resource
from a13n_harness_ui.mcp_runtime.projection import HostToolset
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.tools import ToolResult
from mcp.types import TextContent, Tool
from pydantic_ai import ToolReturn
from pydantic_ai.mcp import MCPError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("hook", ["inject", "redact", "replace", "capture_failure"])
async def test_capture_respects_upstream_hook_semantics_and_never_retries(hook, monkeypatch):
    server = FastMCP("Hook Apps")
    dispatched = []

    @server.tool(meta={"ui": {"resourceUri": "ui://hook/app.html"}})
    def show(value: int, token: str = "") -> ToolResult:
        dispatched.append((value, token))
        return ToolResult(
            content=[TextContent(type="text", text="Shown")],
            structured_content={"value": value},
            meta={"private": "presentation-only"},
        )

    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "hook", "recipe", FastMCPTransport(server))
        toolset = AppToolset(connection, connections.captures)

        async def process(ctx, call, name, arguments):
            if hook == "replace":
                return {"redacted": True}
            arguments["token"] = "injected-secret"
            result = await call(name, arguments)
            return {"redacted": True} if hook == "redact" else result

        toolset.process_tool_call = process
        if hook == "capture_failure":

            def reject_capture(*args):
                raise ValueError("Presentation cannot be serialized")

            monkeypatch.setattr(connections.captures, "add", reject_capture)

        async def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if returns:
                expected = {"redacted": True} if hook in {"redact", "replace"} else {"value": 7}
                assert returns[-1].content == expected
                assert "mcp_app" not in (returns[-1].metadata or {})
                yield "done"
            else:
                yield {0: DeltaToolCall(name="show", json_args='{"value":7}', tool_call_id="show-call")}

        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(AppMCP(id="hook", local=toolset),),
        )
        result = await executable.run("Show seven", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "done"
        assert dispatched == ([] if hook == "replace" else [(7, "injected-secret")])
        captures = connections.captures.take(result.run_id, "show-call")
        assert len(captures) == (1 if hook == "inject" else 0)
        if captures:
            assert captures[0].arguments == {"value": 7}
            assert captures[0].result["_meta"]["private"] == "presentation-only"
            assert captures[0].connection is connection
    finally:
        await connections.close()


@pytest.mark.parametrize("payload", ["valid", "invalid", "wrong_uri"])
async def test_consuming_metadata_preserves_content_and_uses_trusted_binding(payload, monkeypatch):
    declaration = Tool(name="show", input_schema={}, meta={"ui": {"resourceUri": "ui://show/app.html"}})
    client = Client(FastMCP("Metadata fixture"))
    monkeypatch.setattr(client, "list_tools", AsyncMock(return_value=[declaration]))
    connection = SimpleNamespace(server_id="trusted", client=client)
    app = {
        "resource_uri": "ui://other/app.html" if payload == "wrong_uri" else "ui://show/app.html",
        "tool_input": {},
        "result": {"content": [], "isError": False},
        "toolset_id": "untrusted",
    }
    result = ToolReturn(
        return_value={"ok": True},
        content=["supplemental"],
        metadata={"other": {"keep": True}, "mcp_app": app if payload != "invalid" else {}},
    )
    monkeypatch.setattr(HostToolset, "call_tool", AsyncMock(return_value=result))
    captures = Captures()
    toolset = AppToolset(connection, captures)
    ctx = SimpleNamespace(deps=SimpleNamespace(run_id="run-one"), tool_call_id="show-call")
    tool = SimpleNamespace(tool_def=SimpleNamespace(metadata={}))
    actual = await toolset.call_tool("show", {}, ctx, tool)
    assert actual.return_value == {"ok": True}
    assert actual.content == ["supplemental"]
    assert actual.metadata == {"other": {"keep": True}}
    saved = captures.take("run-one", "show-call")
    assert len(saved) == (1 if payload == "valid" else 0)
    if saved:
        assert saved[0].connection.server_id == "trusted"
        assert saved[0].tool is declaration


async def test_app_relay_uses_app_visibility_and_preserves_error_results():
    server = FastMCP("Relay Apps")
    calls = []

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def app_only() -> str:
        calls.append("app")
        return "app-result"

    @server.tool(meta={"ui": {"visibility": ["model"]}})
    def model_only() -> str:
        calls.append("model")
        return "model-result"

    @server.tool()
    def broken() -> str:
        calls.append("broken")
        raise ValueError("business failed")

    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "relay", "recipe", FastMCPTransport(server))
        toolset = HostToolset(connection)

        async def authorize():
            assert connection.dispatch.locked()

        assert (await toolset.call_app_tool("app_only", {}, authorize=authorize))["content"][0]["text"] == "app-result"
        with pytest.raises(MCPError, match="not available to apps"):
            await toolset.call_app_tool("model_only", {}, authorize=authorize)
        error = await toolset.call_app_tool("broken", {}, authorize=authorize)
        assert error["isError"] is True
        assert "business failed" in error["content"][0]["text"]
        assert calls == ["app", "broken"]
        assert not connections.captures._pending
    finally:
        await connections.close()


@pytest.mark.parametrize("variant", ["text", "blob", "wrong_uri", "wrong_mime", "duplicate", "invalid_blob"])
async def test_resource_wire_payload_retains_host_validation(variant, monkeypatch):
    item = {"uri": "ui://test/app.html", "mimeType": MIME_TYPE, "_meta": {"ui": {"csp": {}}}}
    if variant in {"blob", "invalid_blob"}:
        item["blob"] = base64.b64encode(b"<p>App</p>").decode() if variant == "blob" else "not-base64!"
    else:
        item["text"] = "<p>App</p>"
    if variant == "wrong_uri":
        item["uri"] = "ui://other/app.html"
    if variant == "wrong_mime":
        item["mimeType"] = "text/html"
    read = AsyncMock(return_value={"contents": [item, item] if variant == "duplicate" else [item]})
    monkeypatch.setattr(HostToolset, "read_resource_for_app", read)
    connection = SimpleNamespace(server_id="test", client=Client(FastMCP("Resource fixture")))
    if variant in {"text", "blob"}:
        resource = await read_app_resource(connection, "ui://test/app.html")
        assert resource.html == "<p>App</p>"
        assert resource.metadata == {"ui": {"csp": {}}}
    else:
        with pytest.raises(ValueError):
            await read_app_resource(connection, "ui://test/app.html")
    read.assert_awaited_once_with("ui://test/app.html")


async def test_agent_error_does_not_publish_a_successful_app():
    from pydantic_ai.messages import RetryPromptPart

    server = FastMCP("Failing App")
    calls = 0

    @server.tool(meta={"ui": {"resourceUri": "ui://failure/app.html"}})
    def fail() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("business rejected")

    async def model(messages, info):
        failures = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
        if failures:
            assert "business rejected" in str(failures[-1].content)
            yield "reported failure"
        else:
            yield {0: DeltaToolCall(name="fail", json_args="{}", tool_call_id="failed-call")}

    connections = Connections()
    try:
        connection = await connections.acquire("thread-one", "failure", "recipe", FastMCPTransport(server))
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(AppMCP(id="failure", local=AppToolset(connection, connections.captures)),),
        )
        result = await executable.run("Try once", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "reported failure"
        assert calls == 1
        assert not connections.captures._pending
    finally:
        await connections.close()
