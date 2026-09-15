"""Pre-registered app ownership and callback response binding."""

from __future__ import annotations

import json
from asyncio import gather
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_service.connectivity.connections.domain import CreateConnectionRequest, MCPSource
from a13n_service.connectivity.mcp.domain import (
    ConfigureMCPOAuthClientRequest,
    MCPAuthMode,
    MCPOAuthClientInput,
    MCPOAuthSetupRequest,
)
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.models import (
    MCPAuthorizationRecord,
    MCPConnectionOAuthClientRecord,
    MCPConnectionRecord,
)
from a13n_service.storage import transaction
from pydantic import SecretStr, ValidationError

from .conftest import NOW, WORKSPACE_ID, actor
from .connection_helpers import management
from .test_mcp_service import APP_CALLBACK, ISSUER, MCP_ENDPOINT, RemoteServer
from .test_mcp_service import mcp_services as mcp_services


async def create_connection(connections, *, idempotency_key="create-client-test", name="Configured client"):
    return await management(connections).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=idempotency_key,
        request=CreateConnectionRequest(
            name=name,
            source=MCPSource(kind="mcp", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth),
        ),
    )


def app_client():
    return MCPOAuthClientInput(
        issuer_url=ISSUER,
        client_id="user-owned-client",
        client_secret=SecretStr("user-owned-secret"),
        token_endpoint_auth_method="client_secret_post",
        redirect_uri=APP_CALLBACK,
    )


@pytest.mark.anyio
async def test_identical_manual_clients_remain_owned_by_independent_connections(mcp_services):
    connections, oauth, remote = mcp_services
    remote.use_dcr = False
    first = await create_connection(connections, idempotency_key="create-first-client", name="First client")
    second = await create_connection(connections, idempotency_key="create-second-client", name="Second client")

    configured_first = await oauth.configuration.configure(
        actor=actor(),
        connection_id=first.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=first.version, client=app_client()),
    )
    first_client = await oauth.configuration.get(actor=actor(), connection_id=first.id)
    assert first_client is not None
    assert await oauth.configuration.get(actor=actor(), connection_id=second.id) is None

    await oauth.configuration.configure(
        actor=actor(),
        connection_id=second.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=second.version, client=app_client()),
    )
    second_client = await oauth.configuration.get(actor=actor(), connection_id=second.id)
    assert second_client == first_client

    await oauth.configuration.configure(
        actor=actor(),
        connection_id=first.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=configured_first.version, client=None),
    )

    assert first.id != second.id
    assert await oauth.configuration.get(actor=actor(), connection_id=first.id) is None
    assert await oauth.configuration.get(actor=actor(), connection_id=second.id) == second_client


def machine_client():
    return MCPOAuthClientInput(
        issuer_url=ISSUER,
        client_id="machine-client",
        client_secret=SecretStr("machine-secret"),
        token_endpoint_auth_method="client_secret_basic",
        grant_type="client_credentials",
    )


async def launch_for(oauth, connection):
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key=f"authorize-{connection.version}",
        expected_version=connection.version,
        redirect_uri=APP_CALLBACK,
    )
    return launch, parse_qs(urlsplit(launch.authorization_url).query)["state"][0]


@pytest.mark.anyio
async def test_configured_client_is_write_only_survives_authorization_and_is_never_deregistered(
    mcp_services, connectivity_sessions, credential_protector
):
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await create_connection(connections)
    configured = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=created.version, client=app_client()),
    )
    assert not configured.credential_configured
    public = await oauth.configuration.get(actor=actor(), connection_id=created.id)
    assert public is not None
    assert public.client_id == "user-owned-client"
    assert "client_secret" not in public.model_dump()
    assert "user-owned-secret" not in public.model_dump_json()
    async with connectivity_sessions() as session:
        record = await session.get(MCPConnectionOAuthClientRecord, created.id)
        assert record is not None
        protected = json.loads(record.credential_snapshot().decrypt(credential_protector))
        assert protected["client_secret"] == "user-owned-secret"
        assert b"user-owned-secret" not in record.ciphertext
    remote.use_dcr = False
    launch, state = await launch_for(oauth, configured)
    assert parse_qs(urlsplit(launch.authorization_url).query)["client_id"] == ["user-owned-client"]
    ready = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=state,
        code="code",
        issuer=ISSUER,
        response_error=None,
    )
    assert ready.status == "ready"
    token_request = next(request for request in remote.requests if request.url.path == "/token")
    assert parse_qs(token_request.content.decode())["client_secret"] == ["user-owned-secret"]
    assert not any(request.url.path == "/register" for request in remote.requests)
    assert await oauth.configuration.get(actor=actor(), connection_id=ready.id) == public
    await connections.delete(
        actor=actor(), connection_id=ready.id, expected_version=ready.version, idempotency_key="delete-client-test"
    )
    assert not remote.registration_deleted
    async with connectivity_sessions() as session:
        assert await session.get(MCPConnectionOAuthClientRecord, created.id) is None


@pytest.mark.anyio
async def test_replacing_service_registered_client_cleans_only_the_owned_registration(mcp_services):
    connections, oauth, remote = mcp_services
    remote.use_dcr = True
    created = await create_connection(connections)
    await oauth.authorize(
        actor=actor(),
        connection_id=created.id,
        idempotency_key="authorize-owned-client",
        expected_version=created.version,
        redirect_uri=APP_CALLBACK,
    )
    current = await management(connections).get(actor=actor(), connection_id=created.id)

    replaced = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=current.version, client=app_client()),
    )

    assert replaced.version == current.version + 1
    assert remote.registration_deleted is True
    public = await oauth.configuration.get(actor=actor(), connection_id=created.id)
    assert public is not None
    assert public.source == "pre_registered"


@pytest.mark.anyio
async def test_reconfiguration_invalidates_existing_authorization_and_clears_tokens(
    mcp_services, connectivity_sessions
):
    connections, oauth, _ = mcp_services
    created = await create_connection(connections)
    launch, state = await launch_for(oauth, created)
    current = await management(connections).get(actor=actor(), connection_id=created.id)
    configured = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=current.version, client=app_client()),
    )
    assert configured.version == current.version + 1
    assert not configured.credential_configured
    with pytest.raises(MCPConnectionError, match="unavailable"):
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=state,
            code="code",
            issuer=ISSUER,
            response_error=None,
        )
    async with connectivity_sessions() as session:
        setup = await session.get(MCPAuthorizationRecord, launch.id)
        assert setup.status == "expired"
        assert setup.ciphertext is None


@pytest.mark.anyio
async def test_manual_browser_client_is_reused_without_rediscovery(mcp_services):
    connections, oauth, remote = mcp_services
    remote.use_dcr = False
    created = await create_connection(connections)
    setup = await oauth.configuration.setup(
        actor=actor(),
        connection_id=created.id,
        request=MCPOAuthSetupRequest(redirect_uri=APP_CALLBACK),
    )
    assert setup.next_action.type == "configure_oauth_client"
    assert setup.next_action.grant_types == ("authorization_code", "client_credentials")
    configured = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=created.version, client=app_client()),
    )
    requests_after_configuration = len(remote.requests)
    setup = await oauth.configuration.setup(
        actor=actor(),
        connection_id=created.id,
        request=MCPOAuthSetupRequest(redirect_uri=APP_CALLBACK),
    )
    assert setup.next_action.type == "start_authorization"
    assert len(remote.requests) == requests_after_configuration

    launch, state = await launch_for(oauth, configured)
    ready = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=state,
        code="code",
        issuer=ISSUER,
        response_error=None,
    )

    assert ready.status == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("grant", "expected_action"),
    [("authorization_code", "start_authorization"), ("client_credentials", "authenticate_client_credentials")],
)
async def test_setup_reauthorizes_retained_unusable_credentials(
    mcp_services, connectivity_sessions, credential_protector, grant, expected_action
):
    connections, oauth, _ = mcp_services
    created = await create_connection(connections)
    configured = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(
            expected_version=created.version,
            client=app_client() if grant == "authorization_code" else machine_client(),
        ),
    )
    async with transaction(connectivity_sessions) as session:
        connection = await session.get(MCPConnectionRecord, configured.id)
        assert connection is not None
        connection.replace_credential('{"access_token":"unusable"}', credential_protector)
        connection.status = "action_required"
        connection.status_reason = "reauthorization_required"

    setup = await oauth.configuration.setup(
        actor=actor(),
        connection_id=configured.id,
        request=MCPOAuthSetupRequest(redirect_uri=APP_CALLBACK),
    )

    assert setup.next_action.type == expected_action
    assert setup.client is not None
    assert setup.client.grant_type == grant


@pytest.mark.anyio
@pytest.mark.parametrize("issuer_advertised", [False, True])
@pytest.mark.parametrize("registration", ["dcr", "public", "confidential"])
async def test_missing_issuer_is_accepted_with_issuer_specific_callback(
    mcp_services, monkeypatch, issuer_advertised, registration
):
    connections, oauth, remote = mcp_services
    remote.use_dcr = registration == "dcr"
    original = RemoteServer.__call__

    def metadata(self, request):
        response = original(self, request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            body = response.json()
            body["authorization_response_iss_parameter_supported"] = issuer_advertised
            return type(response)(200, json=body)
        return response

    monkeypatch.setattr(RemoteServer, "__call__", metadata)
    connection = await create_connection(connections)
    if registration in {"public", "confidential"}:
        connection = await oauth.configuration.configure(
            actor=actor(),
            connection_id=connection.id,
            request=ConfigureMCPOAuthClientRequest(
                expected_version=connection.version,
                client=app_client()
                if registration == "confidential"
                else MCPOAuthClientInput(
                    issuer_url=ISSUER,
                    client_id="public-client",
                    token_endpoint_auth_method="none",
                    redirect_uri=APP_CALLBACK,
                ),
            ),
        )
    launch, state = await launch_for(oauth, connection)
    ready = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=state,
        code="code",
        issuer=None,
        response_error=None,
    )
    assert ready.status == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("wrong_issuer_value", [f"{ISSUER}/", "https://other.example", ""])
async def test_completion_checks_operation_state_issuer_and_replay_before_token_exchange(
    mcp_services, wrong_issuer_value
):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    launch, state = await launch_for(oauth, connection)
    with pytest.raises(MCPConnectionError) as wrong_state:
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state="wrong-state" * 4,
            code="code",
            issuer=ISSUER,
            response_error=None,
        )
    assert wrong_state.value.code == "invalid_oauth_state"
    with pytest.raises(MCPConnectionError) as wrong_issuer:
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=state,
            code="code",
            issuer=wrong_issuer_value,
            response_error=None,
        )
    assert wrong_issuer.value.code == "oauth_issuer_mismatch"
    assert not any(request.url.path == "/token" for request in remote.requests)
    ready = await oauth.complete(
        actor=actor(),
        authorization_id=launch.id,
        state=state,
        code="code",
        issuer=ISSUER,
        response_error=None,
    )
    assert ready.status == "ready"
    with pytest.raises(MCPConnectionError) as repeated:
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=state,
            code="code",
            issuer=ISSUER,
            response_error=None,
        )
    assert repeated.value.code == "oauth_state_replayed"


@pytest.mark.anyio
async def test_received_response_keeps_original_expiry(mcp_services, connectivity_sessions):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    launch, state = await launch_for(oauth, connection)
    async with transaction(connectivity_sessions) as session:
        setup = await session.get(MCPAuthorizationRecord, launch.id)
        setup.expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(MCPConnectionError) as failure:
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=state,
            code="code",
            issuer=ISSUER,
            response_error=None,
        )
    assert failure.value.code == "oauth_session_expired"
    assert not any(request.url.path == "/token" for request in remote.requests)


@pytest.mark.anyio
async def test_provider_rejection_is_single_use_and_projects_only_a_safe_error(mcp_services, connectivity_sessions):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    launch, state = await launch_for(oauth, connection)

    with pytest.raises(MCPConnectionError) as rejected:
        await oauth.complete(
            actor=actor(),
            authorization_id=launch.id,
            state=state,
            code=None,
            issuer=ISSUER,
            response_error="private-provider-detail",
        )
    assert rejected.value.code == "mcp_oauth_rejected"
    assert not any(request.url.path == "/token" for request in remote.requests)
    async with connectivity_sessions() as session:
        attempt = await session.get(MCPAuthorizationRecord, launch.id)
        assert attempt is not None
        assert attempt.last_error_code == "authorization_rejected"
        assert attempt.ciphertext is None


@pytest.mark.anyio
async def test_concurrent_completion_exchanges_the_code_once(mcp_services):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    launch, state = await launch_for(oauth, connection)

    results = await gather(
        *(
            oauth.complete(
                actor=actor(),
                authorization_id=launch.id,
                state=state,
                code="code",
                issuer=ISSUER,
                response_error=None,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(request.url.path == "/token" for request in remote.requests) == 1


def test_public_client_cannot_hold_a_secret():
    with pytest.raises(ValidationError):
        MCPOAuthClientInput(
            issuer_url=ISSUER,
            client_id="client",
            token_endpoint_auth_method="none",
            client_secret="secret",
        )


def test_client_credentials_requires_authenticated_token_requests():
    with pytest.raises(ValidationError):
        MCPOAuthClientInput(
            issuer_url=ISSUER,
            client_id="client",
            token_endpoint_auth_method="none",
            grant_type="client_credentials",
            client_secret="secret",
        )


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
def test_confidential_client_requires_secret(method):
    with pytest.raises(ValidationError, match="require a secret"):
        MCPOAuthClientInput(
            issuer_url=ISSUER,
            client_id="confidential-client",
            token_endpoint_auth_method=method,
            redirect_uri=APP_CALLBACK,
        )
