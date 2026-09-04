"""Exercise the real upstream client, capability and transport hooks together."""

import json

import httpx2
import pytest
from a13n_harness import AgentContext
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.connectivity.selection_domain import MCPConnectionRunSelection
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.storage import transaction
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from .selection_helpers import MCP_CONNECTION_ID, seed_selection_sources
from .test_mcp_service import MCP_ENDPOINT, RemoteServer, _rpc

pytestmark = pytest.mark.anyio


class ToolServer(RemoteServer):
    def __init__(self):
        super().__init__()
        self.allow_anonymous = True
        self.calls = []

    def __call__(self, request):
        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "POST":
            body = json.loads(request.content)
            if body["method"] == "tools/call":
                self.calls.append((request.headers.get("x-account"), body["params"]["name"]))
                return _rpc(body["id"], {"content": [{"type": "text", "text": "done"}]})
        return super().__call__(request)


@pytest.fixture
async def remote_runtime(connectivity_sessions, credential_protector):
    await seed_selection_sources(connectivity_sessions)
    async with transaction(connectivity_sessions) as session:
        source = await session.get(MCPConnectionRecord, MCP_CONNECTION_ID)
        source.endpoint_url = MCP_ENDPOINT
    server = ToolServer()
    policy = EndpointPolicy()
    transport = RemoteTransport(policy, transport=httpx2.MockTransport(server))
    return ExternalToolRuntime(
        connectivity_sessions, credential_protector, ConnectorProviderRegistry(()), transport, policy
    ), server


async def test_selected_remote_tool_uses_call_guard_and_revocation_stops_dispatch(remote_runtime):
    runtime, server = remote_runtime
    revoked = False
    guards = 0

    async def guard():
        nonlocal guards
        guards += 1
        if revoked:
            raise ValueError("test_lease_revoked")

    selection = MCPConnectionRunSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    async with runtime._mcp(selection, guard) as capability:
        assert capability is not None
        agent = Agent(TestModel(), capabilities=[capability], deps_type=AgentContext)
        await agent.run("search")
        assert server.calls == [(None, "search")]
        revoked = True
        with pytest.raises(Exception, match="test_lease_revoked"):
            await agent.run("search again")
        assert server.calls == [(None, "search")]
    assert guards >= 5


async def test_replacement_discovers_changed_tool_and_missing_explicit_name_fails(remote_runtime):
    runtime, server = remote_runtime

    async def guard():
        pass

    selection = MCPConnectionRunSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    async with runtime._mcp(selection, guard) as capability:
        identity = capability.id
    server.tool_name = "changed"
    with pytest.raises(ValueError, match="selected_tool_unavailable"):
        async with runtime._mcp(selection, guard):
            pytest.fail("missing tool must fail before model execution")
    async with runtime._mcp(selection.model_copy(update={"tools": None}), guard) as capability:
        assert capability.id == identity
        await Agent(TestModel(), capabilities=[capability]).run("use the current tool")
    assert server.calls == [(None, "changed")]


async def test_same_endpoint_clients_keep_accounts_separate_and_refresh_current_headers():
    server = ToolServer()
    transport = RemoteTransport(EndpointPolicy(), transport=httpx2.MockTransport(server))
    first_headers = {"x-account": "one"}

    async def current_headers():
        return dict(first_headers)

    async with transport.connect(MCP_ENDPOINT, headers=dict(first_headers), refresh_headers=current_headers) as first:
        async with transport.connect(MCP_ENDPOINT, headers={"x-account": "two"}) as second:
            await first.call_tool("search", {})
            await second.call_tool("search", {})
            first_headers["x-account"] = "rotated"
            await first.call_tool("search", {})
    assert server.calls == [("one", "search"), ("two", "search"), ("rotated", "search")]


async def test_remote_redirect_is_rejected_without_forwarding_credentials():
    destinations = []

    def redirect(request):
        destinations.append(str(request.url))
        return httpx2.Response(307, headers={"location": "https://1.1.1.1/stolen"})

    transport = RemoteTransport(EndpointPolicy(), transport=httpx2.MockTransport(redirect))
    with pytest.raises(Exception, match="mcp_redirect_rejected"):
        async with transport.connect(MCP_ENDPOINT, headers={"authorization": "Bearer secret"}):
            pytest.fail("redirect cannot initialize a client")
    assert destinations and set(destinations) == {MCP_ENDPOINT}
