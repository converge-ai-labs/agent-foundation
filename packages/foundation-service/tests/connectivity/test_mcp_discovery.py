"""Management discovery refreshes every request and fences its completion."""

import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx2
import pytest
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.management import require_connection
from a13n_service.storage import transaction

from .conftest import NOW, actor
from .test_mcp_refresh import _expired_connection
from .test_mcp_service import RemoteServer, _rpc
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
    result = await connections.reconnect(
        actor=actor(),
        connection_id=ready.id,
        expected_version=ready.version,
        idempotency_key="reconnect-pages",
    )
    assert result.status == "pending"
    assert (await connections.get(actor=actor(), connection_id=result.id)).status == "ready"
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


@pytest.mark.parametrize("change", ["endpoint", "version", "credential", "disabled", "deleted"])
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
            else:
                connection.deleted_at = NOW
                connection.clear_credential()

    monkeypatch.setattr(transport, "connect", invalidate_after_transport)
    with pytest.raises(MCPConnectionError):
        await connections.reconnect(
            actor=actor(),
            connection_id=ready.id,
            expected_version=ready.version,
            idempotency_key="reconnect-race",
        )
