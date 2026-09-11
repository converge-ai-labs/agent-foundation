"""Exercise the real upstream client, capability and transport hooks together."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from a13n_harness import AgentContext
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.execution import AttemptToolScope
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.connectivity.selection_domain import MCPConnectionToolSelection
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, FrozenRunConnectivity
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.storage import transaction
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from .conftest import ORG_ID, WORKSPACE_ID, actor
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
async def remote_runtime(connectivity_sessions, credential_protector, external_runtime_factory):
    await seed_selection_sources(connectivity_sessions)
    async with transaction(connectivity_sessions) as session:
        source = await session.get(MCPConnectionRecord, MCP_CONNECTION_ID)
        source.endpoint_url = MCP_ENDPOINT
    server = ToolServer()
    policy = EndpointPolicy()
    transport = RemoteTransport(policy, transport=httpx2.MockTransport(server))
    return external_runtime_factory(ConnectorProviderRegistry(()), transport, policy), server


@pytest.mark.parametrize("child", [False, True])
async def test_recovery_admission_checks_current_connections_without_opening_clients(
    remote_runtime, connectivity_sessions, monkeypatch, child, execution_authorization
):
    runtime, server = remote_runtime
    selection = MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    selected = FrozenRunConnectivity((), (selection,))
    scope = AttemptToolScope(
        replace(actor(), auth_method="internal"),
        ORG_ID,
        WORKSPACE_ID,
        FrozenRunConnectivity((), ()) if child else selected,
        (),
        authorization=await execution_authorization(),
    )
    read_scope = AsyncMock(return_value=scope)
    monkeypatch.setattr(runtime, "_scope_in_session", read_scope)
    open_clients = Mock(side_effect=AssertionError("Recovery validation must not open tool clients"))
    monkeypatch.setattr(runtime, "_capabilities", open_clients)
    context = Mock(authorization=scope.authorization)
    arguments = {"child_agent_id": "agt_child", "selections": selected} if child else {}

    await runtime.validate(lambda: context, **arguments)
    assert read_scope.await_args.args[1] is context
    assert read_scope.await_args.kwargs["child_agent_id"] == ("agt_child" if child else None)
    async with transaction(connectivity_sessions) as session:
        source = await session.get(MCPConnectionRecord, MCP_CONNECTION_ID)
        source.status = "disabled"
    with pytest.raises(ConnectivitySelectionError, match="mcp_connection_unavailable"):
        await runtime.validate(lambda: context, **arguments)
    open_clients.assert_not_called()
    assert server.calls == []


async def test_selected_remote_tool_uses_call_guard_and_revocation_stops_dispatch(
    remote_runtime, execution_authorization
):
    runtime, server = remote_runtime
    revoked = False
    guards = 0

    async def guard(session=None):
        nonlocal guards
        guards += 1
        if revoked:
            raise ValueError("test_lease_revoked")

    selection = MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    async with runtime._mcp(
        selection,
        guard,
        AttemptToolScope(
            replace(actor(), auth_method="internal"),
            ORG_ID,
            WORKSPACE_ID,
            FrozenRunConnectivity((), (selection,)),
            (),
            authorization=await execution_authorization(),
        ),
    ) as capability:
        assert capability is not None
        agent = Agent(TestModel(), capabilities=[capability], deps_type=AgentContext)
        await agent.run("search")
        assert server.calls == [(None, "search")]
        revoked = True
        with pytest.raises(Exception, match="test_lease_revoked"):
            await agent.run("search again")
        assert server.calls == [(None, "search")]
    assert guards >= 5


async def test_replacement_discovers_changed_tool_and_missing_explicit_name_fails(
    remote_runtime, execution_authorization
):
    runtime, server = remote_runtime

    async def guard(session=None):
        pass

    selection = MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    async with runtime._mcp(
        selection,
        guard,
        AttemptToolScope(
            replace(actor(), auth_method="internal"),
            ORG_ID,
            WORKSPACE_ID,
            FrozenRunConnectivity((), (selection,)),
            (),
            authorization=await execution_authorization(),
        ),
    ) as capability:
        identity = capability.id
    server.tool_name = "changed"
    with pytest.raises(ValueError, match="selected_tool_unavailable"):
        async with runtime._mcp(
            selection,
            guard,
            AttemptToolScope(
                replace(actor(), auth_method="internal"),
                ORG_ID,
                WORKSPACE_ID,
                FrozenRunConnectivity((), (selection,)),
                (),
                authorization=await execution_authorization(),
            ),
        ):
            pytest.fail("missing tool must fail before model execution")
    async with runtime._mcp(
        selection.model_copy(update={"tools": None}),
        guard,
        AttemptToolScope(
            replace(actor(), auth_method="internal"),
            ORG_ID,
            WORKSPACE_ID,
            FrozenRunConnectivity((), (selection,)),
            (),
            authorization=await execution_authorization(),
        ),
    ) as capability:
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


@pytest.mark.parametrize("changed_field", ["version", "credential_generation"])
async def test_replacement_during_authorization_blocks_stale_headers(
    remote_runtime, connectivity_sessions, monkeypatch, changed_field, execution_authorization
):
    runtime, server = remote_runtime
    original = runtime._oauth_refresh.current

    async def replace_after_read(connection_id):
        snapshot = await original(connection_id)
        async with transaction(connectivity_sessions) as session:
            source = await session.get(MCPConnectionRecord, connection_id)
            setattr(source, changed_field, getattr(source, changed_field) + 1)
        return snapshot

    monkeypatch.setattr(runtime._oauth_refresh, "current", replace_after_read)

    async def guard(session=None):
        pass

    selection = MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID, tools=("search",))
    with pytest.raises(ValueError, match="mcp_connection_changed"):
        async with runtime._mcp(
            selection,
            guard,
            AttemptToolScope(
                replace(actor(), auth_method="internal"),
                ORG_ID,
                WORKSPACE_ID,
                FrozenRunConnectivity((), (selection,)),
                (),
                authorization=await execution_authorization(),
            ),
        ):
            pytest.fail("stale headers cannot initialize a client")
    assert not server.requests
