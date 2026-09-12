"""Pre-registered app ownership and callback response binding."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_service.connectivity.mcp.domain import (
    ConfigureMCPOAuthClientRequest,
    CreateMCPConnectionRequest,
    MCPAuthMode,
    MCPOAuthClientInput,
)
from a13n_service.connectivity.mcp.errors import MCPConnectionError
from a13n_service.connectivity.mcp.models import MCPConnectionOAuthClientRecord, MCPOAuthSessionRecord
from a13n_service.connectivity.mcp.oauth_client import issuer_key
from a13n_service.storage import transaction
from pydantic import SecretStr, ValidationError

from .conftest import NOW, WORKSPACE_ID, actor
from .test_mcp_service import ISSUER, MCP_ENDPOINT, RemoteServer, capture_receipt
from .test_mcp_service import mcp_services as mcp_services


async def create_connection(connections):
    return await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-client-test",
        request=CreateMCPConnectionRequest(
            name="Configured client", endpoint_url=MCP_ENDPOINT, auth_mode=MCPAuthMode.oauth
        ),
    )


def app_client():
    return MCPOAuthClientInput(
        issuer_url=ISSUER,
        client_id="user-owned-client",
        client_secret=SecretStr("user-owned-secret"),
        token_endpoint_auth_method="client_secret_post",
    )


async def launch_for(oauth, connection):
    launch = await oauth.authorize(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key=f"authorize-{connection.version}",
        expected_version=connection.version,
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
    receipt = await capture_receipt(oauth, state)
    ready = await oauth.callback(actor=actor(), state=state, receipt=receipt)
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
    )
    current = await connections.get(actor=actor(), connection_id=created.id)

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
@pytest.mark.parametrize("received", [False, True])
async def test_reconfiguration_invalidates_existing_authorization_and_clears_tokens(
    mcp_services, connectivity_sessions, received
):
    connections, oauth, _ = mcp_services
    created = await create_connection(connections)
    launch, state = await launch_for(oauth, created)
    receipt = await capture_receipt(oauth, state) if received else "not-received"
    current = await connections.get(actor=actor(), connection_id=created.id)
    configured = await oauth.configuration.configure(
        actor=actor(),
        connection_id=created.id,
        request=ConfigureMCPOAuthClientRequest(expected_version=current.version, client=app_client()),
    )
    assert configured.version == current.version + 1
    assert not configured.credential_configured
    with pytest.raises(MCPConnectionError, match="unavailable"):
        await oauth.callback(actor=actor(), state=state, receipt=receipt)
    async with connectivity_sessions() as session:
        setup = await session.get(MCPOAuthSessionRecord, launch.id)
        assert setup.status == "expired"
        assert setup.ciphertext is None


@pytest.mark.anyio
@pytest.mark.parametrize("issuer_advertised", [False, True])
@pytest.mark.parametrize("registration", ["cimd", "dcr", "public", "confidential"])
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
                    issuer_url=ISSUER, client_id="public-client", token_endpoint_auth_method="none"
                ),
            ),
        )
    _, state = await launch_for(oauth, connection)
    receipt = await capture_receipt(oauth, state, issuer=None)
    ready = await oauth.callback(actor=actor(), state=state, receipt=receipt)
    assert ready.status == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("wrong_issuer_value", [f"{ISSUER}/", "https://other.example", ""])
async def test_callback_path_issuer_and_receipt_are_all_checked_before_token_exchange(mcp_services, wrong_issuer_value):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    _, state = await launch_for(oauth, connection)
    with pytest.raises(MCPConnectionError) as wrong_path:
        await oauth.receive_callback(
            callback_key=issuer_key(f"{ISSUER}/different"), state=state, code="code", issuer=None
        )
    assert wrong_path.value.code == "oauth_callback_mismatch"
    with pytest.raises(MCPConnectionError) as wrong_issuer:
        await capture_receipt(oauth, state, issuer=wrong_issuer_value)
    assert wrong_issuer.value.code == "oauth_issuer_mismatch"
    receipt = await capture_receipt(oauth, state)
    with pytest.raises(MCPConnectionError) as repeated:
        await capture_receipt(oauth, state)
    assert repeated.value.code == "oauth_session_unavailable"
    with pytest.raises(MCPConnectionError) as wrong_receipt:
        await oauth.callback(actor=actor(), state=state, receipt="wrong-receipt")
    assert wrong_receipt.value.code == "invalid_oauth_receipt"
    assert not any(request.url.path == "/token" for request in remote.requests)
    ready = await oauth.callback(actor=actor(), state=state, receipt=receipt)
    assert ready.status == "ready"


@pytest.mark.anyio
async def test_received_response_keeps_original_expiry(mcp_services, connectivity_sessions):
    connections, oauth, remote = mcp_services
    connection = await create_connection(connections)
    launch, state = await launch_for(oauth, connection)
    receipt = await capture_receipt(oauth, state)
    async with transaction(connectivity_sessions) as session:
        setup = await session.get(MCPOAuthSessionRecord, launch.id)
        setup.expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(MCPConnectionError) as failure:
        await oauth.callback(actor=actor(), state=state, receipt=receipt)
    assert failure.value.code == "oauth_session_expired"
    assert not any(request.url.path == "/token" for request in remote.requests)


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
