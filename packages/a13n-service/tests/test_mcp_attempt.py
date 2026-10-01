"""Attempt-owned clients retain lifetime, not stale per-projection discovery."""

import json

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_service.providers.tools import ToolDispatch
from a13n_service.providers.tools.mcp import mcp_capability, mcp_client
from a13n_service.resources.connections.runtime import _checked
from a13n_service.resources.connections.schemas import ConnectionSelection
from fastmcp import FastMCP
from mcp_types import CLIENT_CAPABILITIES_META_KEY
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_connections import SECRET, create, opened, patch, remote, run_tool, serve

pytestmark = pytest.mark.anyio


async def test_projection_refreshes_catalog_without_owning_client_or_handler():
    server = FastMCP("Changing catalog")

    @server.tool
    def change() -> str:
        @server.tool
        def added(value: int) -> int:
            return value

        return "changed"

    async with serve(server.http_app(path="/mcp")) as url:
        # As in open_http(), native transport owns HTTP entry; the attempt owns
        # fallback closure when setup fails before it reaches the transport.
        http = httpx2.AsyncClient()
        client = mcp_client(url + "/mcp", "connection-one", http, timeout=5)
        async with client:

            async def acquire():
                return client

            async def check(dispatch):
                pass

            capability = mcp_capability("connection-one", acquire, tools=None, defer_loading=False, check=check)
            calls = 0

            async def model(messages, info):
                nonlocal calls
                calls += 1
                names = {tool.name for tool in info.function_tools}
                if calls == 1:
                    assert "added" not in names
                    yield {0: DeltaToolCall(name="change", json_args=json.dumps({}), tool_call_id="change-one")}
                else:
                    assert "added" in names
                    yield "done"

            executable = HarnessBuilder().build(
                AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=(capability,)
            )
            result = await executable.run("Change the catalog", bindings=RunBindings.embedded())
            assert result.output_or_raise() == "done"
            assert client.is_connected()
        assert not client.is_connected()


@pytest.mark.parametrize("refusal", [None, "worker", "connection"])
async def test_state_only_rounds_recheck_authority_without_replaying(service, refusal):
    connection = await create(service, {"config": {"url": "https://mcp.example.test/mcp"}})
    business: list[dict] = []
    dispatches: list[ToolDispatch] = []
    advertised: list[dict] = []

    async def peer(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "server/discover":
            result = {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "cacheScope": "private",
                "ttlMs": 0,
            }
        elif method == "tools/list":
            result = {
                "resultType": "complete",
                "tools": [{"name": "advance", "inputSchema": {"type": "object"}}],
                "cacheScope": "private",
                "ttlMs": 0,
            }
        elif method == "tools/call":
            advertised.append(body["params"]["_meta"][CLIENT_CAPABILITIES_META_KEY])
            business.append(body["params"])
            if len(business) == 1:
                if refusal == "connection":
                    await patch(service, connection, {"enabled": False})
                result = {"resultType": "input_required", "requestState": "opaque-state"}
            else:
                assert body["params"]["requestState"] == "opaque-state"
                result = {"resultType": "complete", "content": [{"type": "text", "text": "completed"}]}
        else:
            raise AssertionError(method)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async def check(dispatch: ToolDispatch) -> None:
        dispatches.append(dispatch)
        if refusal == "worker" and len(dispatches) > 1:
            raise ToolFailed("The worker lost authority; the call was not sent.")

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(peer))
    client = mcp_client(connection["config"]["url"], connection["id"], http, timeout=5)
    async with client:
        assert client.protocol_version == "2026-07-28"

        async def acquire():
            return client

        capability = mcp_capability(
            connection["id"],
            acquire,
            tools=None,
            defer_loading=False,
            check=_checked(service.runtime.storage, connection["id"], check),
        )
        seen = await run_tool((capability,), "advance", {})
        assert client.is_connected()
    assert not client.is_connected()
    assert all(item == {} for item in advertised), "Service must not advertise a human input channel"
    assert len(business) == (2 if refusal is None else 1)
    assert len(dispatches) == (1 if refusal == "connection" else 2)
    assert all(item == ToolDispatch(connection["id"], None, "advance", "call-1") for item in dispatches)
    if refusal is not None:
        assert "not sent" in str(seen["results"])


async def test_attempt_shares_client_across_runs_but_isolates_caller_bindings(service, monkeypatch):
    import a13n_service.resources.connections.runtime as runtime

    clients = []
    original = runtime.mcp_client

    def capture(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(runtime, "mcp_client", capture)
    async with remote() as server:
        connection = await create(
            service,
            {
                "config": {"url": server.url + "/mcp", "headers": ["x-api-key"]},
                "auth": "headers",
                "credential": {"headers": {"x-api-key": SECRET}},
            },
        )
        selection = ConnectionSelection(connection_id=connection["id"])
        dispatches = []
        async with opened(service, [selection], {connection["id"]: {"x-trace": "caller-a"}}, dispatches) as first:
            await run_tool(first, "echo", {"text": "first"})
            assert len(clients) == 1 and clients[0].is_connected()
            assert server.requests[-1]["x-trace"] == "caller-a"
            async with opened(service, [selection], {connection["id"]: {"x-trace": "caller-b"}}, dispatches) as second:
                await run_tool(second, "echo", {"text": "second"})
                assert len(clients) == 2 and clients[1] is not clients[0]
                assert server.requests[-1]["x-trace"] == "caller-b"
            assert clients[0].is_connected() and not clients[1].is_connected()
            await run_tool(first, "echo", {"text": "third"})
            assert len(clients) == 2 and server.requests[-1]["x-trace"] == "caller-a"
        assert not any(client.is_connected() for client in clients)
        assert server.echoed == ["first", "second", "third"]
        assert len(dispatches) == 3
