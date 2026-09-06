"""Demand-driven credentials shared by management discovery and execution."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from a13n_service.connectivity.mcp.management import require_connection
from a13n_service.storage import transaction

from .conftest import NOW, WORKSPACE_ID, actor
from .test_mcp_service import (
    ISSUER,
    MCP_ENDPOINT,
    CreateMCPConnectionRequest,
    MCPAuthMode,
    RemoteServer,
)
from .test_mcp_service import (
    mcp_services as mcp_services,
)
from .test_mcp_service import (
    oauth_refresh as oauth_refresh,
)


async def _authorize_connection(mcp_services):
    connections, oauth, _ = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired",
        request=CreateMCPConnectionRequest(name="Expired", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize",
        expected_version=created.version,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    return await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)


async def _expired_connection(mcp_services, sessions, protector):
    ready = await _authorize_connection(mcp_services)
    async with transaction(sessions) as session:
        connection = await require_connection(session, ready.id, lock=True)
        bundle = json.loads(connection.credential_snapshot().decrypt(protector))
        bundle["access_token"] = "expired-token"
        bundle["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
        connection.replace_credential(json.dumps(bundle), protector)
    mcp_services[2].requests.clear()
    return ready


async def test_reconnect_refreshes_expired_credentials_before_pending_discovery(
    mcp_services, connectivity_sessions, credential_protector, oauth_refresh
):
    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    connections, _, remote = mcp_services
    result = await connections.reconnect(
        actor=actor(),
        connection_id=ready.id,
        expected_version=ready.version,
        idempotency_key="reconnect",
    )
    assert result.status == "pending"
    assert (await connections.get(actor=actor(), connection_id=result.id)).status == "ready"
    assert sum(request.url.path == "/token" for request in remote.requests) == 1
    assert all(request.headers.get("authorization") != "Bearer expired-token" for request in remote.requests)
    assert (await oauth_refresh.current(ready.id)).headers == {"Authorization": "Bearer refreshed-oauth-secret"}


@pytest.mark.parametrize(
    "failure", [httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout, httpx2.ReadTimeout, httpx2.WriteError]
)
async def test_refresh_transport_failure_preserves_only_definitely_unsent_credentials(
    mcp_services, connectivity_sessions, credential_protector, oauth_refresh, monkeypatch, failure
):
    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    connections, _, _ = mcp_services
    original = RemoteServer.__call__

    def fail_token(self, request):
        if request.url.path == "/token":
            raise failure("simulated transport failure", request=request)
        return original(self, request)

    monkeypatch.setattr(RemoteServer, "__call__", fail_token)
    before = await connections.get(actor=actor(), connection_id=ready.id)
    assert await oauth_refresh.ensure_current(ready.id) is False
    current = await connections.get(actor=actor(), connection_id=ready.id)
    unsent = failure in {httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout}
    assert current.status == ("ready" if unsent else "action_required")
    assert current.credential_generation == before.credential_generation
    async with connectivity_sessions() as session:
        connection = await require_connection(session, ready.id)
        assert connection.refresh_claim_owner is None
        assert (
            json.loads(connection.credential_snapshot().decrypt(credential_protector))["refresh_token"]
            == "refresh-secret"
        )
    monkeypatch.setattr(RemoteServer, "__call__", original)
    assert await oauth_refresh.ensure_current(ready.id) is unsent


async def test_competing_refresh_wait_is_bounded_without_invalidating_owner(
    mcp_services, connectivity_sessions, credential_protector
):
    from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh

    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    async with transaction(connectivity_sessions) as session:
        connection = await require_connection(session, ready.id, lock=True)
        connection.refresh_claim_owner = "another-pod"
        connection.refresh_claim_expires_at = NOW + timedelta(seconds=60)
    waiting = OAuthCredentialRefresh(
        connectivity_sessions,
        mcp_services[1]._oauth,
        credential_protector,
        instance_id="waiting",
        wait_seconds=0.01,
        clock=lambda: NOW,
    )
    assert await waiting.ensure_current(ready.id) is False
    async with connectivity_sessions() as session:
        connection = await require_connection(session, ready.id)
        assert connection.status == "ready"
        assert connection.refresh_claim_owner == "another-pod"
    assert not mcp_services[2].requests


async def test_oauth_completion_can_refresh_before_discovery_establishes_ready(mcp_services):
    # Production skew refreshes the mock's one-second token during callback discovery.
    mcp_services[1]._discovery._credentials._skew_seconds = 60
    ready = await _authorize_connection(mcp_services)
    assert ready.status == "ready"
    requests = mcp_services[2].requests
    assert sum(request.url.path == "/token" for request in requests) == 2
    assert any(request.headers.get("authorization") == "Bearer refreshed-oauth-secret" for request in requests)


@pytest.mark.parametrize("separate_instances", [False, True])
async def test_sqlite_concurrent_refresh_claim_exchanges_old_token_once(
    mcp_services, connectivity_sessions, credential_protector, monkeypatch, separate_instances
):
    import asyncio

    from a13n_service.connectivity.mcp import refresh
    from anyio import fail_after

    ready = await _expired_connection(mcp_services, connectivity_sessions, credential_protector)
    original = refresh.require_connection
    both_read = asyncio.Event()
    snapshots = []

    async def read_before_either_claims(session, connection_id, **kwargs):
        connection = await original(session, connection_id, **kwargs)
        if len(snapshots) < 2:
            snapshots.append((connection.refresh_claim_generation, connection.credential_generation))
            assert connection.refresh_claim_owner is None
            if len(snapshots) == 2:
                both_read.set()
            await both_read.wait()
        return connection

    monkeypatch.setattr(refresh, "require_connection", read_before_either_claims)
    first = refresh.OAuthCredentialRefresh(
        connectivity_sessions, mcp_services[1]._oauth, credential_protector, instance_id="first", clock=lambda: NOW
    )
    second = (
        refresh.OAuthCredentialRefresh(
            connectivity_sessions, mcp_services[1]._oauth, credential_protector, instance_id="second", clock=lambda: NOW
        )
        if separate_instances
        else first
    )
    with fail_after(5):
        results = await asyncio.gather(first.current(ready.id), second.current(ready.id), return_exceptions=True)
    assert snapshots[0] == snapshots[1]
    exchanges = [request for request in mcp_services[2].requests if request.url.path == "/token"]
    assert len(exchanges) == 1, "the same rotating refresh token must not be exchanged twice"
    assert all(isinstance(result, refresh.CurrentConnection) for result in results), results
    assert results[0] == results[1]
    assert results[0].headers == {"Authorization": "Bearer refreshed-oauth-secret"}
