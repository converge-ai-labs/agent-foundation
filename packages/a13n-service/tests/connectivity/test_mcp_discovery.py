"""Management discovery refreshes every request and fences its completion."""

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from a13n_service.connectivity.connections.access import ConnectionError
from a13n_service.connectivity.connections.domain import CreateConnectionRequest, MCPSource
from a13n_service.connectivity.mcp.domain import MCPAuthMode
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.management import require_connection
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.storage import transaction
from sqlalchemy import update

from .conftest import NOW, WORKSPACE_ID, actor
from .connection_helpers import management, mcp_checks
from .test_mcp_refresh import _expired_connection
from .test_mcp_service import APP_CALLBACK, MCP_ENDPOINT, RemoteServer, _rpc
from .test_mcp_service import mcp_services as mcp_services


@pytest.mark.parametrize("expiration_boundary", ["initialize", "tools/list"])
async def test_discovery_refreshes_across_handshake_and_pages(
    mcp_services, connectivity_sessions, credential_protector, monkeypatch, expiration_boundary
):
    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    connections, oauth, _ = mcp_services
    now = [NOW]
    oauth._discovery._credentials._clock = lambda: now[0]
    tokens = {}
    requests = []
    used_tokens = []
    original = RemoteServer.__call__

    def respond(self, request):
        if request.url.path == "/token":
            token = f"short-token-{len(tokens)}"
            tokens[token] = now[0] + timedelta(seconds=10)
            return httpx2.Response(
                200,
                json={
                    "access_token": token,
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 10,
                },
            )
        if request.url.path != "/mcp":
            return original(self, request)
        token = request.headers["authorization"].removeprefix("Bearer ")
        used_tokens.append(token)
        assert now[0] < tokens[token], "discovery sent an expired token"
        if request.method == "DELETE":
            return httpx2.Response(204)
        if request.method == "GET":
            return httpx2.Response(405)
        body = json.loads(request.content)
        method = body["method"]
        cursor = body.get("params", {}).get("cursor")
        requests.append((method, cursor, token))
        if method == expiration_boundary and cursor is None:
            now[0] += timedelta(seconds=11)
        if method == "initialize":
            return _rpc(
                body["id"],
                {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "test", "version": "1"},
                    "capabilities": {"tools": {}},
                },
                headers={"mcp-session-id": "session"},
            )
        if method == "notifications/initialized":
            return httpx2.Response(202)
        assert method == "tools/list"
        result = {"tools": [{"name": "first" if cursor is None else "second", "inputSchema": {"type": "object"}}]}
        if cursor is None:
            result["nextCursor"] = "page-2"
        return _rpc(body["id"], result)

    monkeypatch.setattr(RemoteServer, "__call__", respond)
    result = await mcp_checks(connections).check(
        actor=actor(),
        connection_id=ready.id,
        expected_version=ready.version,
    )
    assert result.status == "ready"
    assert await management(connections).get(actor=actor(), connection_id=result.id) == result
    assert len(tokens) == 2
    before = next(token for method, cursor, token in requests if method == expiration_boundary and cursor is None)
    after = next(
        token
        for method, cursor, token in requests
        if (method == "notifications/initialized" if expiration_boundary == "initialize" else cursor == "page-2")
    )
    assert before != after
    assert any(cursor == "page-2" for _, cursor, _ in requests)
    async with connectivity_sessions() as session:
        connection = await require_connection(session, ready.id)
        bundle = json.loads(connection.credential_snapshot().decrypt(credential_protector))
        assert bundle["access_token"] == used_tokens[-1]


@pytest.mark.parametrize("change", ["endpoint", "version", "credential", "disabled", "deleted", "authority"])
async def test_discovery_completion_rejects_concurrent_invalidation(
    mcp_services, connectivity_sessions, credential_protector, monkeypatch, change
):
    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    connections, oauth, _ = mcp_services
    transport = oauth._discovery._transport
    original = transport.connect

    @asynccontextmanager
    async def invalidate_after_transport(*args, **kwargs):
        async with original(*args, **kwargs) as client:
            yield client
        async with transaction(connectivity_sessions) as session:
            connection = await require_connection(session, ready.id, lock=True)
            if change == "endpoint":
                connection.endpoint_url = "https://1.1.1.1/changed"
            elif change == "version":
                connection.version += 1
            elif change == "credential":
                value = connection.credential_snapshot().decrypt(credential_protector)
                connection.replace_credential(value, credential_protector)
            elif change == "disabled":
                connection.status = "disabled"
            elif change == "authority":
                await session.execute(
                    update(RoleBindingRecord)
                    .where(RoleBindingRecord.id == "rb_connectivity_admin")
                    .values(role_key="viewer")
                )
            else:
                connection.deleted_at = NOW
                connection.clear_credential()

    monkeypatch.setattr(transport, "connect", invalidate_after_transport)
    if change in {"endpoint", "credential"}:
        checked = await mcp_checks(connections).check(
            actor=actor(), connection_id=ready.id, expected_version=ready.version
        )
        assert checked.last_check is not None
        assert checked.last_check.status == "unavailable" and checked.last_check.error_code == "connection_changed"
    else:
        with pytest.raises(ConnectionError):
            await mcp_checks(connections).check(actor=actor(), connection_id=ready.id, expected_version=ready.version)


@pytest.mark.parametrize("protocol_version", ["2024-11-05", "2025-03-26"])
async def test_remote_transport_rejects_other_negotiated_protocols(protocol_version):
    from a13n_service.connectivity.mcp.transport import RemoteTransport

    from .connector_helpers import AllowEndpoint

    def respond(request):
        if request.method in {"GET", "DELETE"}:
            return httpx2.Response(405)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return _rpc(
                body["id"],
                {
                    "protocolVersion": protocol_version,
                    "serverInfo": {"name": "old-server", "version": "1"},
                    "capabilities": {"tools": {}},
                },
            )
        assert body["method"] == "notifications/initialized"
        return httpx2.Response(202)

    remote = RemoteTransport(AllowEndpoint(), transport=httpx2.MockTransport(respond))
    with pytest.raises(ValueError, match="mcp_protocol_incompatible"):
        async with remote.connect("https://mcp.example/mcp", headers={}):
            pytest.fail("unsupported negotiation was accepted")


@pytest.mark.parametrize("operation", ["tools", "oauth_callback"])
async def test_discovery_without_receipt_rechecks_authority_before_ready(
    mcp_services, connectivity_sessions, credential_protector, monkeypatch, operation
):
    connections, oauth, _ = mcp_services
    if operation == "tools":
        connection = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
        # Model the durable state left by an interrupted reconnect.
        async with transaction(connectivity_sessions) as session:
            record = await require_connection(session, connection.id, lock=True)
            record.status = "pending"
        state = None
    else:
        connection = await management(connections).create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-authority-test",
            request=CreateConnectionRequest(
                name="OAuth authority test",
                source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
            ),
        )
        launch = await oauth.authorize(
            actor=actor(),
            connection_id=connection.id,
            idempotency_key="authorize-test",
            expected_version=connection.version,
            redirect_uri=APP_CALLBACK,
        )
        state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]

    transport = connections._discovery._transport
    original = transport.connect

    @asynccontextmanager
    async def revoke_after_discovery(*args, **kwargs):
        async with original(*args, **kwargs) as client:
            yield client
        async with transaction(connectivity_sessions) as session:
            await session.execute(
                update(RoleBindingRecord)
                .where(RoleBindingRecord.id == "rb_connectivity_admin")
                .values(role_key="viewer")
            )

    monkeypatch.setattr(transport, "connect", revoke_after_discovery)
    with pytest.raises(MCPConnectionError) as failure:
        if operation == "tools":
            await connections.discover_tools(
                actor=actor(), connection_id=connection.id, expected_version=connection.version
            )
        else:
            assert state is not None
            await oauth.complete(
                actor=actor(),
                authorization_id=launch.id,
                state=state,
                code="code",
                issuer="https://8.8.4.4",
                response_error=None,
            )
    assert failure.value.code == "resource_not_found"
    async with connectivity_sessions() as session:
        record = await require_connection(session, connection.id)
        assert record.status == "pending"
