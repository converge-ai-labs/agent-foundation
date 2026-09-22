from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
import uvicorn
from a13n_harness import AgentSpec, HarnessBuilder, ModelRecoveryPolicy, RunBindings
from a13n_harness.capabilities import CodeActCapability, ToolProxyCapability, ToolProxyGroup
from a13n_harness.mcp import ContextualMCP
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from pydantic_ai.capabilities import WrapperCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _http_mcp(server: FastMCP):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
        http = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        task = asyncio.create_task(http.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not http.started:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            http.should_exit = True
            async with asyncio.timeout(10):
                await task


@pytest.mark.parametrize("codeact", [False, True])
async def test_contextual_mcp_proxy_real_http_isolates_concurrent_runs_and_reuses_recovery_headers(
    codeact: bool,
) -> None:
    server = FastMCP("proxy-context")
    invocations: list[dict[str, str]] = []
    discoveries: list[tuple[str, str]] = []
    headers_calls: list[tuple[str, str]] = []
    attempts: dict[str, int] = {}
    results: dict[str, list] = {}

    class TenantDiscovery(Middleware):
        async def on_list_tools(self, context, call_next):
            headers = get_http_headers()
            assert headers["x-static"] == "static"
            run, tenant = headers["x-run"], headers["x-tenant"]
            assert (run, tenant) in headers_calls
            discoveries.append((run, tenant))
            tools = await call_next(context)
            return [tool for tool in tools if tool.name in {"identity", f"{tenant}_identity"}]

    server.add_middleware(TenantDiscovery())

    @server.tool()
    def alpha_identity() -> str:
        """Return the alpha tenant identity."""
        return "alpha"

    @server.tool()
    def beta_identity() -> str:
        """Return the beta tenant identity."""
        return "beta"

    @server.tool()
    def identity() -> dict[str, str]:
        """Return the authenticated run and tenant headers."""
        headers = get_http_headers()
        assert headers["x-static"] == "static"
        value = {"run": headers["x-run"], "tenant": headers["x-tenant"]}
        invocations.append(value)
        return value

    def headers_factory(context):
        tenant = context.metadata["tenant"]
        headers_calls.append((context.run_id, tenant))
        return {"X-Run": context.run_id, "X-Tenant": tenant}

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        tenant = next(
            text
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for text in ([part.content] if isinstance(part.content, str) else part.content)
            if isinstance(text, str) and text in {"alpha", "beta"}
        )
        # Inspect the live projection, while the server genuinely discovers and executes MCP tools.
        expected = {"search_proxy_tools", "call_proxy_tool"}
        if codeact:
            expected |= {"run_code", "run_program", "store", "load", "forget"}
        assert {tool.name for tool in info.function_tools} == expected
        attempt = attempts.get(tenant, 0)
        attempts[tenant] = attempt + 1
        if attempt == 0:
            yield "partial"
            raise ConnectionResetError("recoverable model disconnect")
        if attempt == 1:
            yield {
                0: DeltaToolCall(
                    name="search_proxy_tools",
                    json_args='{"query":"identity","group":"context"}',
                    tool_call_id="discover",
                )
            }
        elif attempt == 2:
            yield {
                0: DeltaToolCall(
                    name="run_code" if codeact else "call_proxy_tool",
                    json_args=(
                        json.dumps({"code": "await call_proxy_tool(group='context', tool='identity', arguments={})"})
                        if codeact
                        else '{"group":"context","tool":"identity","arguments":{}}'
                    ),
                    tool_call_id="invoke",
                )
            }
        else:
            results[tenant] = [
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            yield "done"

    @dataclass
    class MCPCodeActPolicy(WrapperCapability):
        def get_toolset(self):
            source = self.wrapped.get_toolset()
            if source is None:
                return None
            return CodeActPolicyToolset(source, CodeActToolPolicy(tools={"identity": True}))

    async with _http_mcp(server) as url:
        capability = ContextualMCP(
            url,
            id="context-mcp",
            headers_factory=headers_factory,
            headers={"X-Static": "static"},
            native=False,
            local=True,
        )
        source = MCPCodeActPolicy(capability) if codeact else capability
        capabilities = (ToolProxyCapability(groups={"context": ToolProxyGroup(source, "Current identity")}),)
        if codeact:
            capabilities += (CodeActCapability(),)
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=capabilities,
            model_recovery=ModelRecoveryPolicy(
                enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
            ),
        )
        completed = await asyncio.gather(
            *(
                executable.run(tenant, bindings=RunBindings.embedded(metadata={"tenant": tenant}))
                for tenant in ("alpha", "beta")
            )
        )
    assert all(result.output_or_raise() == "done" for result in completed)
    assert len(headers_calls) == 2
    assert len({run for run, _tenant in headers_calls}) == 2
    assert sorted((item["run"], item["tenant"]) for item in invocations) == sorted(headers_calls)
    assert set(discoveries) == set(headers_calls)
    assert set(results) == {"alpha", "beta"}
    for tenant, returned in results.items():
        tools = {tool["tool"]: tool for tool in returned[0]["tools"]}
        assert set(tools) == {"identity", f"{tenant}_identity"}
        assert tools["identity"]["parameters_json_schema"]["type"] == "object"
        assert tools["identity"]["codeact_eligible"] is codeact


async def test_contextual_mcp_native_path_is_rejected_by_proxy() -> None:
    from .test_tool_proxy import _run

    contextual = ContextualMCP(
        "https://example.invalid/mcp", id="native", headers_factory=lambda ctx: {}, native=True, local=False
    )
    with pytest.raises(ValueError, match="provider-native"):
        await _run((ToolProxyCapability(groups={"native": ToolProxyGroup(contextual, "Native MCP")}),), [])
