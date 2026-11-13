from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpRemoteTransport
from a13n_harness_ui.mcp_adapters import HarnessUiMCP, prepare_mcp_transport
from a13n_harness_ui.mcp_apps.connections import Connections as AppConnections
from a13n_harness_ui.mcp_runtime import client as client_module
from a13n_harness_ui.mcp_runtime.client import InitializingClient
from a13n_harness_ui.mcp_runtime.connections import Connections
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
                server_id="mcp-recovery", transport=McpRemoteTransport(url=url), host_owned=ownership == "host"
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


@pytest.mark.parametrize("failures", [1, 2])
async def test_initialization_recovers_within_same_entry(failures, monkeypatch):
    attempts = 0
    original = Client._negotiate

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            raise TimeoutError("temporary")
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        pass

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    async with InitializingClient(FastMCP("fixture")) as client:
        assert client.is_connected()
        assert attempts == failures + 1
    assert not client.is_connected()


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
            async with InitializingClient(transport, mode=protocol, init_timeout=timeout) as client:
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
                async with InitializingClient(transport, mode=protocol, init_timeout=timeout):
                    pytest.fail("entry must fail")
            # Auto negotiation can probe discovery then legacy initialize, but
            # authentication/configuration failure does not retry client entry.
            assert attempts == (2 if protocol == "auto" else 1)
            assert delays == []


async def test_cancellation_during_initialization_backoff_stops_retry(monkeypatch):
    attempts = 0
    backing_off = asyncio.Event()

    async def enter(self):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("temporary")

    async def backoff(delay):
        backing_off.set()
        await asyncio.Event().wait()

    async def connect():
        async with InitializingClient(FastMCP("fixture")):
            pytest.fail("entry must fail")

    monkeypatch.setattr(Client, "__aenter__", enter)
    monkeypatch.setattr(client_module, "sleep", backoff)
    task = asyncio.create_task(connect())
    await backing_off.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 1


async def test_business_request_failure_is_never_replayed():
    server = FastMCP("fixture")
    calls = 0

    @server.tool
    async def effect() -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("effect may already have happened")

    async with InitializingClient(server) as client:
        result = await client.call_tool_mcp("effect", {})
        assert result.is_error
        assert calls == 1
