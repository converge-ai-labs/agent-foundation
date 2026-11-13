"""Request failures must not be delivered again as independent teardown failures."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import InitializationRetryClient, MCPServer
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _failing_request(failure=None):
    server = FastMCP("request-failure")
    calls = []
    failure = failure or httpx2.ConnectError("private-endpoint-detail")

    @server.tool
    def effect() -> str:
        return "unused"

    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    upstream = httpx2.ASGITransport(app=app)

    async def respond(request):
        method = json.loads(request.content)["method"] if request.method == "POST" else None
        calls.append(method)
        if method == "tools/call":
            if isinstance(failure, MCPError):
                return httpx2.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": json.loads(request.content)["id"],
                        "error": {"code": failure.error.code, "message": failure.error.message},
                    },
                )
            raise failure
        return await upstream.handle_async_request(request)

    def http_client(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), **kwargs)

    transport = StreamableHttpTransport("http://fixture/mcp", httpx_client_factory=http_client)
    async with app.router.lifespan_context(app):
        yield transport, calls, failure


@pytest.mark.parametrize("protocol", ["legacy", "auto"])
@pytest.mark.parametrize("explicit_close", [False, True])
async def test_handled_request_failure_is_not_raised_again_during_client_exit(protocol, explicit_close):
    async with _failing_request() as (transport, calls, failure):
        client = InitializationRetryClient(transport, mode=protocol)
        async with client:
            with pytest.raises(httpx2.ConnectError) as caught:
                await client.call_tool_mcp("effect", {})
            assert caught.value is failure
            if explicit_close:
                await client.close()
        assert not client.is_connected()
        assert calls.count("tools/call") == 1


async def test_proxy_can_complete_after_reported_mcp_request_failure():
    requests = 0
    returns = []

    async with _failing_request() as (transport, calls, _failure):
        client = InitializationRetryClient(transport)

        async def prepare(ctx):
            async def create_toolset():
                return MCPToolset(client, id="remote")

            return create_toolset

        async def model(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield {
                    0: DeltaToolCall(
                        name="call_tool",
                        json_args='{"group":"remote","tool":"effect","arguments":{}}',
                        tool_call_id="call-effect",
                    )
                }
            else:
                returns.extend(
                    part
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                )
                yield "The operation failed; I will not repeat it."

        agent = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                ToolSearchCapability(
                    groups={"remote": ToolSearchGroup(MCPServer(id="remote", prepare=prepare), "Remote tools")}
                ),
            ),
        )
        result = await agent.run("Try the remote operation", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "The operation failed; I will not repeat it."
        assert returns and returns[0].outcome == "failed"
        assert "ConnectError" in str(returns[0].content)
        assert "private-endpoint-detail" not in str(returns[0].content)
        assert calls.count("tools/call") == 1
        assert not client.is_connected()


async def test_nested_concurrent_requests_preserve_native_client_reference_counts():
    async with _failing_request() as (transport, calls, failure):
        client = InitializationRetryClient(transport)
        async with client:
            async with client:
                errors = await asyncio.gather(
                    client.call_tool_mcp("effect", {}), client.call_tool_mcp("effect", {}), return_exceptions=True
                )
                assert all(error is failure for error in errors)
                assert client._session_state.nesting_counter == 2
            assert client._session_state.nesting_counter == 1
        assert client._session_state.nesting_counter == 0
        assert 1 <= calls.count("tools/call") <= 2


async def test_request_failure_propagates_when_not_handled():
    async with _failing_request() as (transport, calls, failure):
        client = InitializationRetryClient(transport)
        with pytest.raises(httpx2.ConnectError) as caught:
            async with client:
                await client.call_tool_mcp("effect", {})
        assert caught.value is failure
        assert calls.count("tools/call") == 1
        assert not client.is_connected()


@pytest.mark.parametrize("message", ["Connection closed", "Non-resumable SSE stream ended"])
async def test_protocol_closed_response_does_not_invalidate_healthy_session(message):
    error = MCPError(code=CONNECTION_CLOSED, message=message)
    async with _failing_request(error) as (transport, calls, _failure):
        client = InitializationRetryClient(transport)
        # A peer can use the same code/message without terminating its session.
        # Bound observation must neither cancel it nor force its contexts closed.
        disconnect_timeout = client._disconnect_timeout
        client._disconnect_timeout = 0
        async with client:
            with pytest.raises(MCPError) as caught:
                await client.call_tool_mcp("effect", {})
            assert caught.value.error == error.error
            assert client.is_connected()
            assert client._session_state.nesting_counter == 1
            assert not client._session_state.session_task.cancelled()
            assert client._reported_session_error is None
            client._disconnect_timeout = disconnect_timeout
            assert [tool.name for tool in await client.list_tools()] == ["effect"]
        assert calls.count("tools/call") == 1


@pytest.mark.parametrize("request_closed", [False, True])
async def test_unobserved_transport_cleanup_failure_still_propagates(monkeypatch, request_closed):
    failure = httpx2.ConnectError("unobserved cleanup failure")
    transport = FastMCPTransport(FastMCP("cleanup-failure"))
    connect = transport.connect_session

    @asynccontextmanager
    async def fail_on_exit(**kwargs):
        async with connect(**kwargs) as session:
            yield session
        raise failure

    monkeypatch.setattr(transport, "connect_session", fail_on_exit)
    client = InitializationRetryClient(transport)
    with pytest.raises(httpx2.ConnectError) as caught:
        async with client:
            if request_closed:

                async def closed(self, coro):
                    coro.close()
                    raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

                # Observation times out before this session fails at teardown.
                with monkeypatch.context() as patch:
                    patch.setattr(Client, "_await_with_session_monitoring", closed)
                    patch.setattr(client, "_disconnect_timeout", 0)
                    with pytest.raises(MCPError, match="Connection closed"):
                        await client.ping()
                assert client._reported_session_error is None
            else:
                assert await client.ping()
    assert caught.value is failure
    assert not client.is_connected()


async def test_new_cleanup_failure_is_not_hidden_by_reported_request_error(monkeypatch):
    exit_client = Client.__aexit__
    cleanup_failure = httpx2.ConnectError("a different cleanup error of the same type")

    async def fail_after_exit(self, *args):
        try:
            await exit_client(self, *args)
        finally:
            raise cleanup_failure

    monkeypatch.setattr(Client, "__aexit__", fail_after_exit)
    async with _failing_request() as (transport, _calls, failure):
        client = InitializationRetryClient(transport)
        with pytest.raises(httpx2.ConnectError) as caught:
            async with client:
                with pytest.raises(httpx2.ConnectError) as request:
                    await client.call_tool_mcp("effect", {})
                assert request.value is failure
        assert caught.value is cleanup_failure
        assert not client.is_connected()


async def test_cancellation_while_observing_session_failure_preserves_session(monkeypatch):
    waiting = asyncio.Event()

    async def closed(self, coro):
        coro.close()
        waiting.set()
        raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

    monkeypatch.setattr(Client, "_await_with_session_monitoring", closed)
    client = InitializationRetryClient(FastMCP("cancellable-observation"))
    async with client:
        session = client._session_state.session_task
        request = asyncio.create_task(client.ping())
        await waiting.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not session.done()
        assert client._session_state.nesting_counter == 1
        assert client._reported_session_error is None
    assert not client.is_connected()


async def test_explicit_close_still_closes_transport_and_reports_new_failure(monkeypatch):
    cleanup_failure = RuntimeError("transport cleanup failed")
    closed = False

    async with _failing_request() as (transport, _calls, failure):

        async def close_transport():
            nonlocal closed
            closed = True
            raise cleanup_failure

        monkeypatch.setattr(transport, "close", close_transport)
        client = InitializationRetryClient(transport)
        async with client:
            with pytest.raises(httpx2.ConnectError) as caught:
                await client.call_tool_mcp("effect", {})
            assert caught.value is failure
            with pytest.raises(RuntimeError) as cleanup:
                await client.close()
            assert cleanup.value is cleanup_failure
            assert closed
            assert not client.is_connected()
