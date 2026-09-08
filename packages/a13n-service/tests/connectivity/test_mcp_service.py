from __future__ import annotations

import json
from asyncio import create_task
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from a13n_service.connectivity.mcp.discovery import MCPDiscoveryService
from a13n_service.connectivity.mcp.domain import (
    CreateMCPConnectionRequest,
    MCPAuthMode,
    ReplaceMCPCredentialsRequest,
    UpdateMCPConnectionRequest,
)
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.management import invalidate_refresh_claim, require_connection
from a13n_service.connectivity.mcp.models import (
    MCPConnectionRecord,
    MCPOAuthSessionRecord,
)
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.oauth_service import MCPOAuthService
from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh
from a13n_service.connectivity.mcp.service import MCPConnectionService
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.storage import transaction
from anyio import Event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor

MCP_ENDPOINT = "https://8.8.8.8/mcp"
ISSUER = "https://8.8.4.4"
PUBLIC_ORIGIN = "https://1.1.1.1"


class RemoteServer:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.allow_anonymous = False
        self.tool_name = "search"
        self.use_dcr = False
        self.registration_deleted = False
        self.refresh_error: str | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/resource-metadata":
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={"resource": MCP_ENDPOINT, "authorization_servers": [ISSUER], "scopes_supported": ["tools"]},
            )
        if request.method == "GET" and path == "/.well-known/oauth-authorization-server":
            client_registration = (
                {"registration_endpoint": f"{ISSUER}/register"}
                if self.use_dcr
                else {"client_id_metadata_document_supported": True}
            )
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "code_challenge_methods_supported": ["S256"],
                    **client_registration,
                },
            )
        if request.method == "POST" and path == "/register":
            return httpx2.Response(
                201,
                headers={"content-type": "application/json"},
                json={
                    "client_id": "dynamic-client",
                    "client_secret": "dynamic-secret",
                    "token_endpoint_auth_method": "client_secret_post",
                    "registration_access_token": "registration-token",
                    "registration_client_uri": f"{ISSUER}/register/dynamic-client",
                },
            )
        if request.method == "DELETE" and path == "/register/dynamic-client":
            assert request.headers["authorization"] == "Bearer registration-token"
            self.registration_deleted = True
            return httpx2.Response(204)
        if request.method == "POST" and path == "/token":
            values = parse_qs(request.content.decode())
            assert values["resource"] == [MCP_ENDPOINT]
            if values["grant_type"] == ["refresh_token"]:
                assert values["refresh_token"] == ["refresh-secret"]
                if self.refresh_error is not None:
                    return httpx2.Response(
                        400,
                        headers={"content-type": "application/json"},
                        json={"error": self.refresh_error},
                    )
                return httpx2.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "access_token": "refreshed-oauth-secret",
                        "refresh_token": "rotated-refresh-secret",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            assert values["code_verifier"][0]
            if self.use_dcr:
                assert values["client_secret"] == ["dynamic-secret"]
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "access_token": "oauth-secret",
                    "refresh_token": "refresh-secret",
                    "token_type": "Bearer",
                    "expires_in": 1,
                },
            )
        if request.method == "DELETE":
            return httpx2.Response(204)
        body = json.loads(request.content)
        has_credentials = "authorization" in request.headers or "x-api-key" in request.headers
        if body["method"] == "initialize" and not has_credentials and not self.allow_anonymous:
            return httpx2.Response(
                401,
                headers={
                    "www-authenticate": (
                        f'Bearer resource_metadata="{MCP_ENDPOINT.removesuffix("/mcp")}/resource-metadata", '
                        'scope="tools"'
                    )
                },
            )
        if body["method"] == "initialize":
            if not self.allow_anonymous:
                assert (
                    request.headers.get("authorization")
                    in {
                        "Bearer bearer-secret",
                        "Bearer oauth-secret",
                        "Bearer refreshed-oauth-secret",
                    }
                    or request.headers.get("x-api-key") == "static-secret"
                )
            return _rpc(
                body["id"],
                {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "test", "version": "1"},
                    "capabilities": {"tools": {}},
                },
                headers={"mcp-session-id": "session"},
            )
        if body["method"] == "notifications/initialized":
            return httpx2.Response(202)
        return _rpc(
            body["id"],
            {"tools": [{"name": self.tool_name, "inputSchema": {"type": "object"}}]},
        )


def _rpc(identifier: int, result: dict[str, object], *, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "application/json", **(headers or {})},
        json={"jsonrpc": "2.0", "id": identifier, "result": result},
    )


@asynccontextmanager
async def service_bundle(connectivity_sessions, credential_protector):
    remote = RemoteServer()
    policy = EndpointPolicy()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(remote), follow_redirects=False) as http_client:
        oauth_client = MCPOAuthClient(http_client, policy)
        discovery = MCPDiscoveryService(
            connectivity_sessions,
            RemoteTransport(policy, transport=httpx2.MockTransport(remote)),
            OAuthCredentialRefresh(
                connectivity_sessions,
                oauth_client,
                credential_protector,
                instance_id="discovery",
                skew_seconds=0,
                clock=lambda: NOW,
            ),
        )
        connections = MCPConnectionService(
            connectivity_sessions,
            policy,
            credential_protector,
            discovery,
            registration_cleaner=oauth_client,
            clock=lambda: NOW,
        )
        oauth = MCPOAuthService(
            connectivity_sessions,
            oauth_client,
            credential_protector,
            discovery,
            public_origin=PUBLIC_ORIGIN,
            client_name="Service Test",
            instance_id="mcp-test",
            clock=lambda: NOW,
        )
        yield connections, oauth, remote


@pytest.fixture
async def mcp_services(connectivity_sessions, credential_protector):
    async with service_bundle(connectivity_sessions, credential_protector) as bundle:
        yield bundle


@pytest.mark.anyio
async def test_bearer_connection_is_pending_until_credentials_then_becomes_ready(mcp_services) -> None:
    connections, _oauth, _remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-bearer",
        request=CreateMCPConnectionRequest(
            name="Bearer MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.bearer,
        ),
    )
    assert created.status == "pending"
    assert created.credential_configured is False

    ready = await connections.replace_credentials(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="bearer-credentials",
        request=ReplaceMCPCredentialsRequest(expected_version=1, bearer="bearer-secret"),
    )
    assert ready.status == "pending"
    assert (await connections.get(actor=actor(), connection_id=ready.id)).status == "ready"
    assert ready.credential_configured is True
    assert "bearer-secret" not in repr(ready)

    replay = await connections.replace_credentials(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="bearer-credentials",
        request=ReplaceMCPCredentialsRequest(expected_version=1, bearer="bearer-secret"),
    )
    assert replay == ready


@pytest.mark.anyio
async def test_management_tool_preview_rechecks_version_and_authority(
    mcp_services, connectivity_sessions, monkeypatch
) -> None:
    connections, _oauth, remote = mcp_services
    remote.allow_anonymous = True
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="preview-connection",
        request=CreateMCPConnectionRequest(name="Preview", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none),
    )
    tools = await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=1)
    assert [tool.name for tool in tools.items] == ["search"]
    original = connections._discovery.discover

    async def changed_during_discovery(connection_id):
        result = await original(connection_id)
        await connections.update(
            actor=actor(),
            connection_id=connection_id,
            request=UpdateMCPConnectionRequest(name="Changed", expected_version=1),
        )
        return result

    monkeypatch.setattr(connections._discovery, "discover", changed_during_discovery)
    with pytest.raises(MCPConnectionError, match="changed concurrently"):
        await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=1)

    async def revoked_during_discovery(connection_id):
        result = await original(connection_id)
        async with transaction(connectivity_sessions) as session:
            await session.execute(
                update(RoleBindingRecord)
                .where(RoleBindingRecord.id == "rb_connectivity_admin")
                .values(role_key="viewer")
            )
        return result

    monkeypatch.setattr(connections._discovery, "discover", revoked_during_discovery)
    with pytest.raises(MCPConnectionError):
        await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=2)
    count = len(remote.requests)
    with pytest.raises(MCPConnectionError):
        await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=2)
    assert len(remote.requests) == count


@pytest.mark.anyio
async def test_personal_connection_is_concealed_from_another_principal(mcp_services) -> None:
    connections, _oauth, _remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-private",
        request=CreateMCPConnectionRequest(
            name="Private MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.bearer,
        ),
    )
    other = AuthenticatedActor(
        principal=PrincipalRef(principal_type="service_account", principal_id=SERVICE_ACCOUNT_ID),
        auth_method="bearer",
        credential_id="token-test",
        boundary_workspace_id=WORKSPACE_ID,
    )
    assert (await connections.get(actor=other, connection_id=created.id)).id == created.id


@pytest.mark.anyio
async def test_viewer_cannot_manage_workspace_connections(mcp_services, connectivity_sessions):
    connections, _oauth, _remote = mcp_services
    async with transaction(connectivity_sessions) as session:
        await session.execute(
            update(RoleBindingRecord).where(RoleBindingRecord.id == "rb_connectivity_admin").values(role_key="viewer")
        )
    with pytest.raises(MCPConnectionError):
        await connections.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="viewer",
            request=CreateMCPConnectionRequest(
                name="Unauthorized", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none
            ),
        )


@pytest.mark.anyio
async def test_static_headers_require_the_complete_immutable_name_set(mcp_services) -> None:
    connections, _oauth, remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-static",
        request=CreateMCPConnectionRequest(
            name="Static MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.static_headers,
            static_header_names=("X-API-Key",),
        ),
    )
    with pytest.raises(MCPConnectionError, match="invalid"):
        await connections.replace_credentials(
            actor=actor(),
            connection_id=created.id,
            idempotency_key="invalid-static",
            request=ReplaceMCPCredentialsRequest(
                expected_version=1,
                static_headers={"X-Other": "static-secret"},
            ),
        )
    ready = await connections.replace_credentials(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="valid-static",
        request=ReplaceMCPCredentialsRequest(
            expected_version=1,
            static_headers={"x-api-key": "static-secret"},
        ),
    )
    assert ready.status == "pending"
    assert (await connections.get(actor=actor(), connection_id=ready.id)).status == "ready"
    assert any(request.headers.get("x-api-key") == "static-secret" for request in remote.requests)
    assert "static-secret" not in repr(ready)


@pytest.mark.anyio
async def test_oauth_state_is_bound_single_use_and_callback_validates_connection(mcp_services) -> None:
    connections, oauth, _remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-oauth",
        request=CreateMCPConnectionRequest(
            name="OAuth MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-oauth",
        expected_version=1,
    )
    replay = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-oauth",
        expected_version=1,
    )
    assert replay == launch
    query = parse_qs(urlsplit(launch.authorization_url).query)
    assert query["resource"] == [MCP_ENDPOINT]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == [f"{PUBLIC_ORIGIN}/api/v1/oauth/mcp/client-metadata.json"]

    other_user = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id="usr_0123456789abcdef"),
        auth_method="session",
        credential_id="other-session",
        boundary_workspace_id=WORKSPACE_ID,
    )
    with pytest.raises(MCPConnectionError, match="state is invalid"):
        await oauth.callback(actor=other_user, state=query["state"][0], code="code", issuer=ISSUER)

    ready = await oauth.callback(actor=actor(), state=query["state"][0], code="code", issuer=ISSUER)
    assert ready.status == "ready"
    assert ready.credential_configured is True
    with pytest.raises(MCPConnectionError, match="already used"):
        await oauth.callback(actor=actor(), state=query["state"][0], code="code", issuer=ISSUER)


@pytest.mark.anyio
async def test_none_connection_discovers_immediately_and_lifecycle_is_versioned(mcp_services) -> None:
    connections, _oauth, remote = mcp_services
    remote.allow_anonymous = True
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-none",
        request=CreateMCPConnectionRequest(
            name="Anonymous MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.none,
        ),
    )
    assert created.status == "pending"
    created = await connections.get(actor=actor(), connection_id=created.id)
    assert created.status == "ready"
    disabled = await connections.set_enabled(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="disable-none",
        expected_version=1,
        enabled=False,
    )
    assert disabled.status == "disabled"
    enabled = await connections.set_enabled(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="enable-none",
        expected_version=2,
        enabled=True,
    )
    assert enabled.status == "ready"


@pytest.mark.anyio
async def test_delete_fences_connection_and_cleans_exact_dcr_registration(
    mcp_services,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-dcr",
        request=CreateMCPConnectionRequest(
            name="DCR MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-dcr",
        expected_version=1,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)
    await connections.delete(
        actor=actor(),
        connection_id=ready.id,
        idempotency_key="delete-dcr",
        expected_version=2,
    )

    assert remote.registration_deleted is True
    with pytest.raises(MCPConnectionError, match="requested resource"):
        await connections.get(actor=actor(), connection_id=ready.id)
    async with connectivity_sessions() as session:
        deleted = await session.get(MCPConnectionRecord, ready.id)
    assert deleted is not None
    assert deleted.deleted_at is not None
    assert deleted.ciphertext is deleted.nonce is deleted.encryption_key_id is None
    async with connectivity_sessions() as session:
        completed = await session.get(MCPOAuthSessionRecord, launch.id)
    assert completed is not None
    assert completed.ciphertext is completed.nonce is completed.encryption_key_id is None


@pytest.mark.anyio
async def test_oauth_refresh_rotates_bundle_without_coupling_readiness_to_discovery(
    mcp_services, oauth_refresh
) -> None:
    connections, oauth, remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-refresh",
        request=CreateMCPConnectionRequest(
            name="Refresh MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-refresh",
        expected_version=1,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)

    requests_before = len(remote.requests)
    assert await oauth_refresh.ensure_current(ready.id) is True
    refreshed = await connections.get(actor=actor(), connection_id=ready.id)
    assert refreshed.status == "ready"
    assert refreshed.credential_generation == ready.credential_generation + 1
    assert all(request.url.path == "/token" for request in remote.requests[requests_before:])
    await connections.reconnect(
        actor=actor(), connection_id=ready.id, expected_version=refreshed.version, idempotency_key="reconnect-refreshed"
    )
    assert any(request.headers.get("authorization") == "Bearer refreshed-oauth-secret" for request in remote.requests)


@pytest.mark.anyio
async def test_oauth_invalid_grant_requires_reauthorization(mcp_services, oauth_refresh) -> None:
    connections, oauth, remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-grant",
        request=CreateMCPConnectionRequest(
            name="Invalid Grant MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-invalid-grant",
        expected_version=1,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)
    remote.refresh_error = "invalid_grant"

    assert await oauth_refresh.ensure_current(ready.id) is False
    failed = await connections.get(actor=actor(), connection_id=ready.id)
    assert failed.status == "action_required"
    assert failed.status_reason == "reauthorization_required"
    assert failed.credential_generation == ready.credential_generation


@pytest.mark.anyio
async def test_oauth_refresh_lost_race_does_not_replace_newer_credentials(
    mcp_services,
    oauth_refresh,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections, oauth, _remote = mcp_services
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-refresh-race",
        request=CreateMCPConnectionRequest(
            name="Refresh Race MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-refresh-race",
        expected_version=1,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)
    started = Event()
    proceed = Event()

    async def delayed_refresh(bundle: dict[str, object]) -> dict[str, object]:
        started.set()
        await proceed.wait()
        return {**bundle, "access_token": "stale-candidate", "expires_in": 3600}

    monkeypatch.setattr(oauth._oauth, "refresh", delayed_refresh)
    refresh_task = create_task(oauth_refresh.ensure_current(ready.id))
    await started.wait()
    async with transaction(connectivity_sessions) as session:
        connection = await require_connection(session, ready.id, lock=True)
        connection.replace_credential(
            json.dumps(
                {
                    "kind": "oauth",
                    "access_token": "newer-token",
                    "refresh_token": "newer-refresh",
                    "token_type": "Bearer",
                }
            ),
            credential_protector,
        )
        invalidate_refresh_claim(connection, now=NOW)
    proceed.set()

    assert await refresh_task is False
    current = await connections.get(actor=actor(), connection_id=ready.id)
    assert current.credential_generation == ready.credential_generation + 1


@pytest.mark.anyio
async def test_new_oauth_authorization_expires_and_cleans_prior_dcr_session(
    mcp_services,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-oauth-restart",
        request=CreateMCPConnectionRequest(
            name="OAuth Restart MCP",
            endpoint_url=MCP_ENDPOINT,
            auth_mode=MCPAuthMode.oauth,
        ),
    )
    first = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-first",
        expected_version=1,
    )
    await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-second",
        expected_version=2,
    )
    async with connectivity_sessions() as session:
        expired = await session.scalar(
            select(MCPOAuthSessionRecord)
            .where(
                MCPOAuthSessionRecord.mcp_connection_id == created.id,
                MCPOAuthSessionRecord.status == "expired",
            )
            .order_by(MCPOAuthSessionRecord.created_at, MCPOAuthSessionRecord.id)
        )
    assert expired is not None
    assert expired.ciphertext is expired.nonce is expired.encryption_key_id is None
    assert remote.registration_deleted is False
    state = parse_qs(urlsplit(first.authorization_url).query)["state"][0]
    with pytest.raises(MCPConnectionError, match="unavailable"):
        await oauth.callback(actor=actor(), state=state, code="code", issuer=ISSUER)


@pytest.fixture
def oauth_refresh(mcp_services, connectivity_sessions, credential_protector):
    return OAuthCredentialRefresh(
        connectivity_sessions,
        mcp_services[1]._oauth,
        credential_protector,
        instance_id="refresh-test",
        clock=lambda: NOW,
    )


@pytest.mark.parametrize("invalidation", ["abandoned", "disabled", "deleted"])
async def test_postgresql_cross_pod_callback_and_single_refresh(
    postgres_connectivity_sessions, credential_protector, monkeypatch, invalidation
):
    from datetime import timedelta

    sessions = postgres_connectivity_sessions
    async with service_bundle(sessions, credential_protector) as (connections, pod_a, _remote):
        created = await connections.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="cross-pod",
            request=CreateMCPConnectionRequest(
                name="Cross Pod", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth
            ),
        )
        launch = await pod_a.authorize(
            actor=actor(), connection_id=created.id, idempotency_key="authorize", expected_version=1
        )
        pod_b = MCPOAuthService(
            sessions,
            pod_a._oauth,
            credential_protector,
            pod_a._discovery,
            public_origin=PUBLIC_ORIGIN,
            client_name="Service Test",
            instance_id="pod-b",
            clock=lambda: NOW,
        )
        state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
        ready = await pod_b.callback(actor=actor(), state=state, code="code", issuer=ISSUER)
        assert ready.status == "ready"
        entered, release = Event(), Event()
        original_refresh = pod_a._oauth.refresh
        calls = []

        async def delayed(bundle):
            calls.append(bundle)
            entered.set()
            await release.wait()
            return await original_refresh(bundle)

        monkeypatch.setattr(pod_a._oauth, "refresh", delayed)
        first = OAuthCredentialRefresh(
            sessions, pod_a._oauth, credential_protector, instance_id="pod-a", clock=lambda: NOW
        )
        second = OAuthCredentialRefresh(
            sessions, pod_a._oauth, credential_protector, instance_id="pod-b", clock=lambda: NOW
        )
        running = create_task(first.current(ready.id))
        await entered.wait()
        waiting = Event()
        original_claim = second._claim

        async def observe_claim(connection_id):
            claim = await original_claim(connection_id)
            waiting.set()
            return claim

        monkeypatch.setattr(second, "_claim", observe_claim)
        competing = create_task(second.current(ready.id))
        await waiting.wait()
        assert not competing.done()
        release.set()
        assert (
            (await running).headers == (await competing).headers == {"Authorization": "Bearer refreshed-oauth-secret"}
        )
        assert await second.ensure_current(ready.id) is True
        assert len(calls) == 1
        if invalidation != "abandoned":
            async with transaction(sessions) as session:
                connection = await require_connection(session, ready.id, lock=True)
                bundle = json.loads(connection.credential_snapshot().decrypt(credential_protector))
                bundle["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
                connection.replace_credential(json.dumps(bundle), credential_protector)
                version = connection.version
            entered = Event()
            release = Event()
            running = create_task(first.ensure_current(ready.id))
            await entered.wait()
            if invalidation == "disabled":
                await connections.set_enabled(
                    actor=actor(),
                    connection_id=ready.id,
                    idempotency_key="disable-refresh",
                    expected_version=version,
                    enabled=False,
                )
            else:
                await connections.delete(
                    actor=actor(), connection_id=ready.id, idempotency_key="delete-refresh", expected_version=version
                )
            release.set()
            assert await running is False
            async with sessions() as session:
                connection = await session.get(MCPConnectionRecord, ready.id)
                assert connection.status == "disabled"
                assert connection.refresh_claim_owner is None
                if invalidation == "deleted":
                    assert connection.ciphertext is None
            return
        async with transaction(sessions) as session:
            connection = await require_connection(session, ready.id, lock=True)
            connection.refresh_claim_owner = "crashed-pod"
            connection.refresh_claim_expires_at = NOW - timedelta(seconds=1)
        assert await second.ensure_current(ready.id) is False
        assert len(calls) == 1
        assert (await connections.get(actor=actor(), connection_id=ready.id)).status == "action_required"
