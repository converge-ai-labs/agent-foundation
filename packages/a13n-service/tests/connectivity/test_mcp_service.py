from __future__ import annotations

import json
from asyncio import create_task
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connections.access import ConnectionError
from a13n_service.connectivity.connections.domain import CreateConnectionRequest, MCPSource, UpdateConnectionRequest
from a13n_service.connectivity.mcp.discovery import MCPDiscoveryService
from a13n_service.connectivity.mcp.domain import (
    ConfigureMCPOAuthClientRequest,
    MCPAuthMode,
    MCPOAuthClientInput,
    ReplaceMCPCredentialsRequest,
)
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.management import invalidate_refresh_claim, require_connection
from a13n_service.connectivity.mcp.models import (
    MCPAuthorizationRecord,
    MCPConnectionOAuthClientRecord,
    MCPConnectionRecord,
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
from .connection_helpers import management, mcp_checks

MCP_ENDPOINT = "https://8.8.8.8/mcp"
ISSUER = "https://8.8.4.4"
APP_CALLBACK = "https://app.example/callback"


async def stored_generation(service, connection_id):
    async with transaction(service._sessions) as session:
        record = await require_connection(session, connection_id)
        return record.credential_generation


class RemoteServer:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.allow_anonymous = False
        self.tool_name = "search"
        self.use_dcr = True
        self.registration_deleted = False
        self.refresh_error: str | None = None
        self.machine_error: str | None = None
        self.machine_token_count = 0
        self.verification_unavailable = False
        self.machine_expires_in = 1

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
            client_registration = {"registration_endpoint": f"{ISSUER}/register"} if self.use_dcr else {}
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
                    "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
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
            if values["grant_type"] == ["client_credentials"]:
                self.machine_token_count += 1
                assert request.headers["authorization"].startswith("Basic ")
                assert values["scope"] == ["tools"]
                if self.machine_error is not None:
                    return httpx2.Response(
                        400,
                        headers={"content-type": "application/json"},
                        json={"error": self.machine_error},
                    )
                return httpx2.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "access_token": f"machine-oauth-secret-{self.machine_token_count}",
                        "token_type": "Bearer",
                        "expires_in": self.machine_expires_in,
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
            if self.verification_unavailable:
                raise httpx2.ConnectError("MCP temporarily unavailable", request=request)
            if not self.allow_anonymous:
                assert (
                    request.headers.get("authorization")
                    in {
                        "Bearer bearer-secret",
                        "Bearer oauth-secret",
                        "Bearer refreshed-oauth-secret",
                    }
                    or request.headers.get("authorization", "").startswith("Bearer machine-oauth-secret-")
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
            redirect_uris=(APP_CALLBACK,),
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
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-bearer",
        request=CreateConnectionRequest(
            name="Bearer MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.bearer)
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
    assert ready.status == "ready"
    assert (await management(connections).get(actor=actor(), connection_id=ready.id)).status == "ready"
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
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="preview-connection",
        request=CreateConnectionRequest(
            name="Preview", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none)
        ),
    )
    tools = await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=1)
    assert [tool.name for tool in tools.items] == ["search"]
    original = connections._discovery.discover

    async def changed_during_discovery(connection_id, **kwargs):
        result = await original(connection_id, **kwargs)
        await management(connections).update(
            actor=actor(),
            connection_id=connection_id,
            request=UpdateConnectionRequest(name="Changed", expected_version=1),
        )
        return result

    monkeypatch.setattr(connections._discovery, "discover", changed_during_discovery)
    with pytest.raises(MCPConnectionError, match="changed concurrently"):
        await connections.discover_tools(actor=actor(), connection_id=created.id, expected_version=1)

    async def revoked_during_discovery(connection_id, **kwargs):
        result = await original(connection_id, **kwargs)
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
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-private",
        request=CreateConnectionRequest(
            name="Private MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.bearer)
        ),
    )
    other = AuthenticatedActor(
        principal=PrincipalRef(principal_type="service_account", principal_id=SERVICE_ACCOUNT_ID),
        auth_method="bearer",
        credential_id="token-test",
        boundary_workspace_id=WORKSPACE_ID,
    )
    assert (await management(connections).get(actor=other, connection_id=created.id)).id == created.id


@pytest.mark.anyio
async def test_viewer_cannot_manage_workspace_connections(mcp_services, connectivity_sessions):
    connections, _oauth, _remote = mcp_services
    async with transaction(connectivity_sessions) as session:
        await session.execute(
            update(RoleBindingRecord).where(RoleBindingRecord.id == "rb_connectivity_admin").values(role_key="viewer")
        )
    with pytest.raises(ConnectionError):
        await management(connections).create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="viewer",
            request=CreateConnectionRequest(
                name="Unauthorized", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none)
            ),
        )


@pytest.mark.anyio
async def test_static_headers_require_the_complete_immutable_name_set(mcp_services) -> None:
    connections, _oauth, remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-static",
        request=CreateConnectionRequest(
            name="Static MCP",
            source=MCPSource(
                kind="mcp",
                endpoint_url=MCP_ENDPOINT,
                auth_mode=MCPAuthMode.static_headers,
                static_header_names=("X-API-Key",),
            ),
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
    assert ready.status == "ready"
    assert (await management(connections).get(actor=actor(), connection_id=ready.id)).status == "ready"
    assert any(request.headers.get("x-api-key") == "static-secret" for request in remote.requests)
    assert "static-secret" not in repr(ready)


@pytest.mark.anyio
async def test_oauth_state_is_bound_single_use_and_callback_validates_connection(mcp_services) -> None:
    connections, oauth, _remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-oauth",
        request=CreateConnectionRequest(
            name="OAuth MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth)
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-oauth",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    replay = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-oauth",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    assert replay == launch
    query = parse_qs(urlsplit(launch.authorization_url).query)
    assert query["resource"] == [MCP_ENDPOINT]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["dynamic-client"]

    other_user = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id="usr_0123456789abcdef"),
        auth_method="session",
        credential_id="other-session",
        boundary_workspace_id=WORKSPACE_ID,
    )
    with pytest.raises(MCPConnectionError, match="state is invalid"):
        await oauth.complete(
            actor=other_user,
            authorization_id=launch.id,
            state=query["state"][0],
            code="code",
            issuer=ISSUER,
            response_error=None,
        )

    ready = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=query["state"][0],
        code="code",
        issuer=ISSUER,
        response_error=None,
    )
    assert ready.status == "ready"
    assert ready.credential_configured is True
    with pytest.raises(MCPConnectionError, match="already used"):
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=query["state"][0],
            code="code",
            issuer=ISSUER,
            response_error=None,
        )


@pytest.mark.anyio
async def test_oauth_callback_preserves_credentials_when_verification_is_temporarily_unavailable(
    mcp_services, monkeypatch
) -> None:
    connections, oauth, remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-verification-retry",
        request=CreateConnectionRequest(
            name="OAuth verification retry",
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-verification-retry",
        expected_version=created.version,
        redirect_uri=APP_CALLBACK,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    original = oauth._discovery.discover

    async def unavailable(*args, **kwargs):
        raise MCPConnectionError(
            "mcp_discovery_unavailable",
            "Remote tool discovery failed.",
            category=ErrorCategory.unavailable,
        )

    monkeypatch.setattr(oauth._discovery, "discover", unavailable)
    pending = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=state,
        code="code",
        issuer=ISSUER,
        response_error=None,
    )
    assert pending.status == "pending"
    assert pending.credential_configured is True
    assert sum(request.url.path == "/token" for request in remote.requests) == 1

    monkeypatch.setattr(oauth._discovery, "discover", original)
    ready = await mcp_checks(connections).check(
        actor=actor(),
        connection_id=pending.id,
        expected_version=pending.version,
    )
    assert ready.status == "ready"
    assert sum(request.url.path == "/token" for request in remote.requests) == 1


@pytest.mark.anyio
async def test_none_connection_requires_check_and_lifecycle_is_versioned(mcp_services) -> None:
    connections, _oauth, remote = mcp_services
    remote.allow_anonymous = True
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-none",
        request=CreateConnectionRequest(
            name="Anonymous MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.none)
        ),
    )
    assert created.status == "pending"
    assert remote.requests == []
    created = await mcp_checks(connections).check(
        actor=actor(), connection_id=created.id, expected_version=created.version
    )
    assert created.status == "ready"
    assert await management(connections).get(actor=actor(), connection_id=created.id) == created
    disabled = await management(connections).set_enabled(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="disable-none",
        expected_version=1,
        enabled=False,
    )
    assert disabled.status == "disabled"
    enabled = await management(connections).set_enabled(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="enable-none",
        expected_version=2,
        enabled=True,
    )
    assert enabled.status == "pending"
    checked = await mcp_checks(connections).check(
        actor=actor(), connection_id=enabled.id, expected_version=enabled.version
    )
    assert checked.status == "ready"


@pytest.mark.anyio
async def test_delete_fences_connection_and_cleans_exact_dcr_registration(
    mcp_services,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-dcr",
        request=CreateConnectionRequest(
            name="DCR MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth)
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-dcr",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.complete(
        actor=actor(), authorization_id=launch.id, state=state, code="code", issuer=ISSUER, response_error=None
    )
    await connections.delete(
        actor=actor(),
        connection_id=ready.id,
        idempotency_key="delete-dcr",
        expected_version=2,
    )

    assert remote.registration_deleted is True
    with pytest.raises(ConnectionError, match="requested resource"):
        await management(connections).get(actor=actor(), connection_id=ready.id)
    async with connectivity_sessions() as session:
        deleted = await session.get(MCPConnectionRecord, ready.id)
    assert deleted is not None
    assert deleted.deleted_at is not None
    assert deleted.ciphertext is deleted.nonce is deleted.encryption_key_id is None
    async with connectivity_sessions() as session:
        completed = await session.get(MCPAuthorizationRecord, launch.id)
    assert completed is not None
    assert completed.ciphertext is completed.nonce is completed.encryption_key_id is None


@pytest.mark.anyio
async def test_oauth_refresh_rotates_bundle_without_coupling_readiness_to_discovery(
    mcp_services, oauth_refresh
) -> None:
    connections, oauth, remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-refresh",
        request=CreateConnectionRequest(
            name="Refresh MCP", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth)
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-refresh",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.complete(
        actor=actor(), authorization_id=launch.id, state=state, code="code", issuer=ISSUER, response_error=None
    )
    original_generation = await stored_generation(connections, ready.id)

    requests_before = len(remote.requests)
    assert await oauth_refresh.ensure_current(ready.id) is True
    refreshed = await management(connections).get(actor=actor(), connection_id=ready.id)
    assert refreshed.status == "ready"
    assert refreshed.authorization_generation == ready.authorization_generation
    assert await stored_generation(connections, ready.id) == original_generation + 1
    assert all(request.url.path == "/token" for request in remote.requests[requests_before:])
    await mcp_checks(connections).check(
        actor=actor(),
        connection_id=ready.id,
        expected_version=refreshed.version,
    )
    assert any(request.headers.get("authorization") == "Bearer refreshed-oauth-secret" for request in remote.requests)


@pytest.mark.anyio
@pytest.mark.parametrize("verification_unavailable", [False, True])
async def test_client_credentials_acquires_and_renews_without_browser_authorization(
    mcp_services, oauth_refresh, connectivity_sessions, credential_protector, verification_unavailable
) -> None:
    connections, oauth, remote = mcp_services
    machine_oauth = MCPOAuthService(
        connectivity_sessions,
        oauth._oauth,
        credential_protector,
        oauth._discovery,
        redirect_uris=(),
        client_name="Service Test",
        instance_id="machine-test",
        clock=lambda: NOW,
    )
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-machine-oauth",
        request=CreateConnectionRequest(
            name="Machine OAuth MCP",
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )
    machine_client = MCPOAuthClientInput(
        issuer_url=ISSUER,
        client_id="machine-client",
        client_secret="machine-secret",
        token_endpoint_auth_method="client_secret_basic",
        grant_type="client_credentials",
    )
    configured = await machine_oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(
            expected_version=created.version,
            client=machine_client,
        ),
    )

    remote.requests.clear()
    remote.verification_unavailable = verification_unavailable
    authenticated = await machine_oauth.authenticate_client_credentials(
        actor=actor(),
        connection_id=configured.id,
        idempotency_key="authenticate-machine-oauth",
        expected_version=configured.version,
    )
    assert remote.requests[0].url.path == "/token"
    assert authenticated.credential_configured is True
    if verification_unavailable:
        assert authenticated.status == "pending"
        remote.verification_unavailable = False
        ready = await mcp_checks(connections).check(
            actor=actor(),
            connection_id=authenticated.id,
            expected_version=authenticated.version,
        )
    else:
        ready = authenticated
    assert ready.status == "ready"
    assert remote.machine_token_count == 1
    assert not any(request.url.path == "/authorize" for request in remote.requests)
    assert not any(request.url.path == "/register" for request in remote.requests)
    async with connectivity_sessions() as session:
        connection = await session.get(MCPConnectionRecord, ready.id)
        client = await session.get(MCPConnectionOAuthClientRecord, ready.id)
        assert connection is not None
        assert client is not None
        token = json.loads(connection.credential_snapshot().decrypt(credential_protector))
        protected_client = json.loads(client.credential_snapshot().decrypt(credential_protector))
        assert token["grant_type"] == "client_credentials"
        assert not {"client_id", "client_secret", "token_endpoint"}.intersection(token)
        assert client.configuration_json["client_id"] == "machine-client"
        assert protected_client["client_secret"] == "machine-secret"

    replayed = await machine_oauth.authenticate_client_credentials(
        actor=actor(),
        connection_id=configured.id,
        idempotency_key="authenticate-machine-oauth",
        expected_version=configured.version,
    )
    assert replayed == authenticated
    assert remote.machine_token_count == 1

    original_generation = await stored_generation(connections, ready.id)
    assert await oauth_refresh.ensure_current(ready.id) is True
    renewed = await management(connections).get(actor=actor(), connection_id=ready.id)
    assert renewed.status == "ready"
    assert renewed.authorization_generation == ready.authorization_generation
    assert await stored_generation(connections, ready.id) == original_generation + 1
    assert remote.machine_token_count == 2

    reconfigured = await machine_oauth.configuration.configure(
        actor=actor(),
        connection_id=renewed.id,
        request=ConfigureMCPOAuthClientRequest(
            expected_version=renewed.version,
            client=machine_client,
        ),
    )
    remote.machine_error = "invalid_client"
    with pytest.raises(MCPConnectionError) as rejected:
        await machine_oauth.authenticate_client_credentials(
            actor=actor(),
            connection_id=reconfigured.id,
            idempotency_key="authenticate-rejected-machine-oauth",
            expected_version=reconfigured.version,
        )
    assert rejected.value.code == "mcp_oauth_rejected"


@pytest.mark.anyio
async def test_oauth_invalid_grant_requires_reauthorization(mcp_services, oauth_refresh) -> None:
    connections, oauth, remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invalid-grant",
        request=CreateConnectionRequest(
            name="Invalid Grant MCP",
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-invalid-grant",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.complete(
        actor=actor(), authorization_id=launch.id, state=state, code="code", issuer=ISSUER, response_error=None
    )
    original_generation = await stored_generation(connections, ready.id)
    remote.refresh_error = "invalid_grant"

    assert await oauth_refresh.ensure_current(ready.id) is False
    failed = await management(connections).get(actor=actor(), connection_id=ready.id)
    assert failed.status == "action_required"
    assert failed.status_reason == "reauthorization_required"
    assert failed.authorization_generation == ready.authorization_generation
    assert await stored_generation(connections, ready.id) == original_generation


@pytest.mark.anyio
async def test_oauth_refresh_lost_race_does_not_replace_newer_credentials(
    mcp_services,
    oauth_refresh,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections, oauth, _remote = mcp_services
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-refresh-race",
        request=CreateConnectionRequest(
            name="Refresh Race MCP",
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-refresh-race",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
    ready = await oauth.complete(
        actor=actor(), authorization_id=launch.id, state=state, code="code", issuer=ISSUER, response_error=None
    )
    original_generation = await stored_generation(connections, ready.id)
    started = Event()
    proceed = Event()

    async def delayed_refresh(bundle: dict[str, object], _client) -> dict[str, object]:
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
    current = await management(connections).get(actor=actor(), connection_id=ready.id)
    assert current.authorization_generation == ready.authorization_generation
    assert await stored_generation(connections, ready.id) == original_generation + 1


@pytest.mark.anyio
async def test_new_oauth_authorization_expires_and_cleans_prior_dcr_session(
    mcp_services,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-oauth-restart",
        request=CreateConnectionRequest(
            name="OAuth Restart MCP",
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )
    first = await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-first",
        expected_version=1,
        redirect_uri=APP_CALLBACK,
    )
    await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-second",
        expected_version=2,
        redirect_uri=APP_CALLBACK,
    )
    assert sum(request.url.path == "/register" for request in remote.requests) == 1
    async with connectivity_sessions() as session:
        expired = await session.scalar(
            select(MCPAuthorizationRecord)
            .where(
                MCPAuthorizationRecord.connection_id == created.id,
                MCPAuthorizationRecord.status == "expired",
            )
            .order_by(MCPAuthorizationRecord.created_at, MCPAuthorizationRecord.id)
        )
    assert expired is not None
    assert expired.ciphertext is expired.nonce is expired.encryption_key_id is None
    assert remote.registration_deleted is False
    state = parse_qs(urlsplit(first.authorization_url).query)["state"][0]
    with pytest.raises(MCPConnectionError, match="unavailable"):
        await oauth.complete(
            actor=actor(),
            authorization_id=first.id,
            state=state,
            code="code",
            issuer=ISSUER,
            response_error=None,
        )


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
@pytest.mark.parametrize("grant", ["authorization_code", "client_credentials"])
async def test_postgresql_cross_pod_authorization_and_single_refresh(
    connectivity_sessions, credential_protector, monkeypatch, invalidation, grant
):
    from datetime import timedelta

    sessions = connectivity_sessions
    async with service_bundle(sessions, credential_protector) as (connections, pod_a, remote):
        created = await management(connections).create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="cross-pod",
            request=CreateConnectionRequest(
                name="Cross Pod", source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth)
            ),
        )
        pod_b = MCPOAuthService(
            sessions,
            pod_a._oauth,
            credential_protector,
            pod_a._discovery,
            redirect_uris=(APP_CALLBACK,),
            client_name="Service Test",
            instance_id="pod-b",
            clock=lambda: NOW,
        )
        if grant == "client_credentials":
            configured = await pod_a.configuration.configure(
                actor=actor(),
                connection_id=created.id,
                request=ConfigureMCPOAuthClientRequest(
                    expected_version=created.version,
                    client=MCPOAuthClientInput(
                        issuer_url=ISSUER,
                        client_id="machine-client",
                        client_secret="machine-secret",
                        token_endpoint_auth_method="client_secret_basic",
                        grant_type="client_credentials",
                    ),
                ),
            )
            ready = await pod_b.authenticate_client_credentials(
                actor=actor(),
                connection_id=created.id,
                idempotency_key="authenticate",
                expected_version=configured.version,
            )
            remote.machine_expires_in = 3600
            expected_token = "machine-oauth-secret-2"
        else:
            launch = await pod_a.authorize(
                actor=actor(),
                connection_id=created.id,
                idempotency_key="authorize",
                expected_version=1,
                redirect_uri=APP_CALLBACK,
            )
            state = parse_qs(urlsplit(launch.authorization_url).query)["state"][0]
            ready = await pod_b.complete(
                actor=actor(),
                authorization_id=launch.id,
                state=state,
                code="code",
                issuer=ISSUER,
                response_error=None,
            )
            expected_token = "refreshed-oauth-secret"
        assert ready.status == "ready"
        entered, release = Event(), Event()
        original_refresh = pod_a._oauth.refresh
        calls = []

        async def delayed(bundle, client):
            calls.append(bundle)
            entered.set()
            await release.wait()
            return await original_refresh(bundle, client)

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
        assert (await running).headers == (await competing).headers == {"Authorization": f"Bearer {expected_token}"}
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
                await management(connections).set_enabled(
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
        assert (await management(connections).get(actor=actor(), connection_id=ready.id)).status == "action_required"
