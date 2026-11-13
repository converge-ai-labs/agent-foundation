from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness import mcp as client_module
from a13n_harness.mcp import InitializationRetryClient
from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpRemoteTransport
from a13n_harness_ui.mcp_adapters import HarnessUiMCP, prepare_mcp_transport
from a13n_harness_ui.mcp_apps.connections import Connections as AppConnections
from a13n_harness_ui.mcp_runtime.connections import Connections
from a13n_harness_ui.mcp_runtime.transports import ObservedTransport
from fastmcp import Client, FastMCP
from mcp import MCPError
from pydantic_ai.models.function import FunctionModel

from .test_tool_search_integration import _http_mcp

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("ownership", ["local", "input", "host", "apps"])
async def test_initialization_retries_then_later_run_recovers(ownership, monkeypatch):
    attempts = 0
    failures = 3
    delays = []
    original = Client._negotiate

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            try:
                raise TimeoutError("fixture timeout")
            except TimeoutError as exc:
                raise RuntimeError("Failed to initialize server session") from exc
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        delays.append(delay)
        await asyncio.sleep(0)

    async def input_handler(*args):
        raise AssertionError("No input requested")

    async def model(messages, info):
        yield "recovered"

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    connections = (
        AppConnections()
        if ownership == "apps"
        else Connections(input_handler=input_handler if ownership == "input" else None)
    )
    async with _http_mcp(FastMCP("Recovery fixture")) as url:
        capability = HarnessUiMCP(
            ResolvedMcpRecipe(
                server_id="mcp-recovery",
                transport=McpRemoteTransport(url=url),
                host_owned=ownership == "host",
                on_error="raise",
            ),
            connections=connections,
            apps=connections if ownership == "apps" else None,
        )
        agent = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
        )
        try:
            with pytest.raises(RuntimeError, match="Failed to initialize"):
                await agent.run(
                    "Fail this input",
                    previous_state=HarnessState.new(thread_id="thread_recovery"),
                    bindings=RunBindings.embedded(),
                )
            assert attempts == 3
            assert delays == [1, 2]
            assert (
                await agent.run(
                    "Fresh opportunity",
                    previous_state=HarnessState.new(thread_id="thread_recovery"),
                    bindings=RunBindings.embedded(),
                )
            ).output_or_raise() == "recovered"
            assert attempts == 4
        finally:
            await connections.close()


@pytest.mark.parametrize("protocol", ["legacy", "auto"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 502, 503, 504])
async def test_only_transient_http_initialization_errors_retry(status, protocol, monkeypatch):
    attempts = calls = 0
    delays = []
    transient = status in {408, 429, 502, 503, 504}
    server = FastMCP("HTTP recovery")
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    upstream = httpx2.ASGITransport(app=app)

    async def respond(request):
        nonlocal attempts, calls
        method = json.loads(request.content)["method"] if request.method == "POST" else None
        if method in {"initialize", "server/discover"}:
            attempts += 1
            if attempts == 1 or (not transient and status != 404):
                return httpx2.Response(status, text="fixture failure")
        if method == "tools/call":
            calls += 1
            return httpx2.Response(503, text="effect may already have happened")
        return await upstream.handle_async_request(request)

    async def backoff(delay):
        delays.append(delay)

    # Use the production HTTP factory, hooks and SDK negotiation. Only the wire
    # transport is replaced so SDK error conversion remains under test.
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda **kwargs: httpx2.MockTransport(respond))
    monkeypatch.setattr(client_module, "sleep", backoff)
    transport, _, timeout = await prepare_mcp_transport(
        ResolvedMcpRecipe(
            server_id="mcp-http", transport=McpRemoteTransport(url="http://127.0.0.1/mcp"), protocol=protocol
        ),
        None,
    )
    async with app.router.lifespan_context(app):
        if transient or (protocol == "auto" and status == 404):
            async with InitializationRetryClient(transport, mode=protocol, init_timeout=timeout) as client:
                assert client.is_connected() and attempts == 2
                # An identical status after readiness remains an ordinary tool
                # failure. It neither retries the effect nor loses the client.
                with pytest.raises(MCPError):
                    await client.call_tool_mcp("effect", {})
                assert calls == 1 and client.is_connected()
                # A missing modern endpoint uses SDK protocol fallback, without
                # spending a retry or its backoff.
                assert delays == ([1] if transient else [])
        else:
            with pytest.raises(MCPError):
                async with InitializationRetryClient(transport, mode=protocol, init_timeout=timeout):
                    pytest.fail("entry must fail")
            # Auto negotiation can probe discovery then legacy initialize, but
            # authentication/configuration failure does not retry client entry.
            assert attempts == (2 if protocol == "auto" else 1)
            assert delays == []


@pytest.mark.parametrize("ownership", ["local", "input", "host", "apps"])
@pytest.mark.parametrize("transient", [False, True])
@pytest.mark.parametrize("selection", ["default", "agent", "server"])
async def test_default_skip_keeps_other_tools_and_recovers_on_later_run(
    ownership, transient, selection, monkeypatch, caplog
):
    attempts = 0
    available = False
    prompts = []
    inventories = []
    clients = set()
    original = Client._negotiate
    original_for_run = HarnessUiMCP.for_run

    async def for_run(self, ctx):
        first = await original_for_run(self, ctx)
        count = attempts
        assert await original_for_run(self, ctx) is first
        assert attempts == count
        return first

    recovering = FastMCP("recovering")
    healthy = FastMCP("healthy")

    @recovering.tool
    def recovered_tool() -> str:
        return "recovered"

    @healthy.tool
    def healthy_tool() -> str:
        return "healthy"

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        clients.add(self)
        transport = self.transport.wrapped if isinstance(self.transport, ObservedTransport) else self.transport
        if str(transport.url) == unavailable_url:
            attempts += 1
            if not available:
                if transient:
                    raise TimeoutError("private-server-response")
                raise ValueError("private-server-response")
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        await asyncio.sleep(0)

    async def input_handler(*args):
        raise AssertionError("No input requested")

    async def model(messages, info):
        prompts.append(str(messages) + str(info.instructions))
        inventories.append({tool.name for tool in info.function_tools})
        yield "continued"

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    connections = (
        AppConnections()
        if ownership == "apps"
        else Connections(input_handler=input_handler if ownership == "input" else None)
    )
    monkeypatch.setattr(HarnessUiMCP, "for_run", for_run)
    async with _http_mcp(recovering) as unavailable_url, _http_mcp(healthy) as healthy_url:
        capabilities = tuple(
            HarnessUiMCP(
                ResolvedMcpRecipe(
                    server_id=identity,
                    transport=McpRemoteTransport(url=url),
                    tools=(
                        (("recovered_tool",) if identity == "mcp-recovering" else ("healthy_tool",))
                        if selection == "server"
                        else None
                    ),
                    host_owned=ownership == "host",
                ),
                connections=connections,
                apps=connections if ownership == "apps" else None,
            )
            for identity, url in (("mcp-recovering", unavailable_url), ("mcp-healthy", healthy_url))
        )
        if selection != "default":
            from a13n_harness_ui.composition.reconstruction import _ToolAllowlistCapability

            capabilities = (*capabilities, _ToolAllowlistCapability(frozenset({"healthy_tool", "recovered_tool"})))
        agent = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=capabilities
        )
        try:
            state = HarnessState.new(thread_id="thread_recovery")
            result = await agent.run(
                "Continue despite missing server", previous_state=state, bindings=RunBindings.embedded()
            )
            assert result.output_or_raise() == "continued"
            assert attempts == (3 if transient else 1)
            assert inventories[0] == {"healthy_tool"}
            assert "mcp-recovering is unavailable" in prompts[0]
            assert "private-server-response" not in prompts[0]
            warnings = [
                record
                for record in caplog.records
                if record.name.endswith(".mcp")
                and record.getMessage() == "MCP server skipped for this Run after startup failure"
            ]
            assert len(warnings) == 1 and warnings[0].server_id == "mcp-recovering"
            assert "private-server-response" not in warnings[0].getMessage()
            available = True
            result = await agent.run("Try again", previous_state=result.state, bindings=RunBindings.embedded())
            assert result.output_or_raise() == "continued"
            assert attempts == (4 if transient else 2)
            assert inventories[1] == {"healthy_tool", "recovered_tool"}
            if ownership in {"local", "input"}:
                assert not any(client.is_connected() for client in clients)
            else:
                assert all(connection._borrows == 0 for connection in connections.current())
                assert all(connection.connected for connection in connections.current())
        finally:
            await connections.close()
        assert not any(client.is_connected() for client in clients)


async def test_default_skip_does_not_hide_transport_authorization_failure():
    from a13n_harness.configuration import HostNotAllowedError, RunConfiguration

    capability = HarnessUiMCP(
        ResolvedMcpRecipe(server_id="mcp-denied", transport=McpRemoteTransport(url="https://denied.example/mcp"))
    )

    async def model(messages, info):
        pytest.fail("Denied transport must fail before model execution")
        yield "unreachable"

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(capability,),
    )
    with pytest.raises(HostNotAllowedError, match=r"denied\.example"):
        result = await agent.run(
            "Must not skip policy",
            bindings=RunBindings.embedded(configuration=RunConfiguration(allowed_hosts=frozenset({"allowed.example"}))),
        )
        result.output_or_raise()


@pytest.mark.parametrize("ownership", ["local", "input", "host", "apps"])
async def test_default_skip_propagates_run_cancellation_during_startup(ownership, monkeypatch):
    backing_off = asyncio.Event()
    clients = set()

    async def negotiate(self, *args, **kwargs):
        clients.add(self)
        raise TimeoutError("temporary")

    async def backoff(delay):
        backing_off.set()
        await asyncio.Event().wait()

    async def model(messages, info):
        pytest.fail("Cancelled startup must not execute the model")
        yield "unreachable"

    async def input_handler(*args):
        raise AssertionError("No input requested")

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    connections = (
        AppConnections()
        if ownership == "apps"
        else Connections(input_handler=input_handler if ownership == "input" else None)
    )
    async with _http_mcp(FastMCP("cancelled")) as url:
        capability = HarnessUiMCP(
            ResolvedMcpRecipe(
                server_id="mcp-cancel", transport=McpRemoteTransport(url=url), host_owned=ownership == "host"
            ),
            connections=connections,
            apps=connections if ownership == "apps" else None,
        )
        agent = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
        )
        task = asyncio.create_task(agent.run("Cancel startup", bindings=RunBindings.embedded()))
        try:
            await asyncio.wait_for(backing_off.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await connections.close()
        assert not any(client.is_connected() for client in clients)


async def test_default_skip_does_not_swallow_post_ready_tool_discovery_errors(monkeypatch):
    from pydantic_ai.mcp import MCPToolset

    async def get_tools(self, ctx):
        raise RuntimeError("post-ready tool discovery failed")

    async def model(messages, info):
        pytest.fail("Discovery error must not be treated as skipped initialization")
        yield "unreachable"

    monkeypatch.setattr(MCPToolset, "get_tools", get_tools)
    async with _http_mcp(FastMCP("ready")) as url:
        capability = HarnessUiMCP(ResolvedMcpRecipe(server_id="mcp-ready", transport=McpRemoteTransport(url=url)))
        agent = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
        )
        result = await agent.run("List tools", bindings=RunBindings.embedded())
        assert result.status == "failed"
        assert result.failure.details["exception_type"] == "RuntimeError"
