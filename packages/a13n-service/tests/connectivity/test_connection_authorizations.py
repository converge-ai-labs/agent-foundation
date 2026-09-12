"""Application principals complete authorization without a Console identity."""

from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_service.connectivity.connections.access import ConnectionError
from a13n_service.connectivity.connections.authorization import AuthorizationService
from a13n_service.connectivity.connections.domain import (
    CompleteAuthorizationRequest,
    CreateAuthorizationRequest,
    CreateConnectionRequest,
    LaunchAuthorizationRequest,
    ReceiveAuthorizationRequest,
)
from a13n_service.connectivity.connections.handoff import digest
from a13n_service.connectivity.connections.models import AuthorizationRecord
from a13n_service.connectivity.connections.service import ConnectionService
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.storage import transaction
from pydantic import SecretStr

from .conftest import SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor
from .test_composio_setup import composio_sessions as composio_sessions
from .test_composio_setup import composio_setup as composio_setup
from .test_mcp_service import ISSUER, MCP_ENDPOINT, issuer_key, service_bundle

pytestmark = pytest.mark.anyio
VERIFIER = "v" * 64
RETURN_URL = "https://customer.example/oauth/complete"


async def app_actor(sessions) -> AuthenticatedActor:
    async with transaction(sessions) as session:
        binding = await session.get(RoleBindingRecord, "rb_connectivity_runner")
        assert binding is not None
        binding.role_key = "builder"
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="service_account", principal_id=SERVICE_ACCOUNT_ID),
        auth_method="api_key",
        credential_id="key_customer",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req_customer",
    )


def browser_request(version: int, *, connector: bool = False) -> CreateAuthorizationRequest:
    return CreateAuthorizationRequest(
        expected_version=version,
        method="browser",
        return_url=RETURN_URL,
        state="application-state-" + "s" * 32,
        completion_challenge=digest(VERIFIER),
        options={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"} if connector else {},
    )


async def browser_roundtrip(service: AuthorizationService, auth, *, session_uri: str | None = None):
    assert auth.next_action is not None and auth.next_action.url is not None
    launch = parse_qs(urlsplit(auth.next_action.url).fragment)
    token = launch["token"][0]
    target = await service.launch(auth.id, LaunchAuthorizationRequest(token=token, browser_nonce="b" * 64))
    assert target.url.startswith("https://")
    received = await service.receive(
        auth.id, ReceiveAuthorizationRequest(browser_nonce="b" * 64, session_uri=session_uri)
    )
    query = parse_qs(urlsplit(received.url).query)
    assert received.url.startswith(RETURN_URL)
    assert query["authorization_id"] == [auth.id]
    return CompleteAuthorizationRequest(receipt=query["receipt"][0], completion_verifier=VERIFIER)


async def test_composio_application_backend_owns_completion_and_replay(composio_setup, credential_protector):
    connector, connection, _, sessions, _requests, state, now = composio_setup
    principal = await app_actor(sessions)
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        auth = await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="customer-start",
            request=browser_request(connection.version, connector=True),
        )
        assert auth.status == "awaiting_user"
        replay = await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="customer-start",
            request=browser_request(connection.version, connector=True),
        )
        assert replay.next_action == auth.next_action
        assert state["link_calls"] == 1
        proof = await browser_roundtrip(service, auth, session_uri="opaque-provider-session")
        with pytest.raises(ConnectionError):
            await service.complete(actor=actor(), authorization_id=auth.id, request=proof)
        with pytest.raises(ConnectionError):
            await service.complete(
                actor=principal,
                authorization_id=auth.id,
                request=proof.model_copy(update={"completion_verifier": "x" * 64}),
            )
        completed = await service.complete(actor=principal, authorization_id=auth.id, request=proof)
        assert completed.status == "completed"
        assert (await service.complete(actor=principal, authorization_id=auth.id, request=proof)).status == "completed"
        assert state["redeem_calls"] == 1
        assert "must-not-escape" not in completed.model_dump_json()
        async with transaction(sessions) as session:
            attempt = await session.get(AuthorizationRecord, auth.id)
            assert attempt is not None and attempt.ciphertext is None


async def test_mcp_application_backend_and_cross_tab_binding(composio_setup, credential_protector):
    connector, _, _, sessions, _, _, now = composio_setup
    principal = await app_actor(sessions)
    common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://1.1.1.1",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        connection = await common.create(
            actor=principal,
            workspace_id=WORKSPACE_ID,
            idempotency_key="mcp-create",
            request=CreateConnectionRequest.model_validate(
                {"name": "Remote", "source": {"kind": "mcp", "endpoint_url": MCP_ENDPOINT, "auth_mode": "oauth"}}
            ),
        )
        auth = await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="mcp-start",
            request=browser_request(connection.version),
        )
        launch = parse_qs(urlsplit(auth.next_action.url).fragment)
        target = await service.launch(
            auth.id, LaunchAuthorizationRequest(token=launch["token"][0], browser_nonce="b" * 64)
        )
        with pytest.raises(ConnectionError):
            await service.launch(auth.id, LaunchAuthorizationRequest(token=launch["token"][0], browser_nonce="c" * 64))
        state = parse_qs(urlsplit(target.url).query)["state"][0]
        redirected = await oauth.receive_callback(
            callback_key=issuer_key(ISSUER), state=state, code="code", issuer=ISSUER
        )
        assert redirected.endswith("/connection-authorizations/browser")
        proof = await browser_roundtrip(service, auth)
        result = await service.complete(actor=principal, authorization_id=auth.id, request=proof)
        assert result.status == "completed"
        current = await common.get(actor=principal, connection_id=connection.id)
        assert current.status == "ready" and current.authorization_generation == 2


async def test_mcp_direct_credentials_are_queryable_and_create_is_local(composio_setup, credential_protector):
    connector, _, _, sessions, _, _, now = composio_setup
    principal = await app_actor(sessions)
    common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, remote):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin=None,
            return_urls=(),
            clock=lambda: now[0],
        )
        connection = await common.create(
            actor=principal,
            workspace_id=WORKSPACE_ID,
            idempotency_key="direct-create",
            request=CreateConnectionRequest.model_validate(
                {"name": "Remote", "source": {"kind": "mcp", "endpoint_url": MCP_ENDPOINT, "auth_mode": "bearer"}}
            ),
        )
        assert remote.requests == []
        auth = await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="direct-auth",
            request=CreateAuthorizationRequest(
                expected_version=connection.version,
                method="credentials",
                credentials={"bearer": SecretStr("bearer-secret")},
            ),
        )
        assert auth.status == "completed"
        assert (await service.get(actor=principal, authorization_id=auth.id)).status == "completed"
        assert (await common.get(actor=principal, connection_id=connection.id)).status == "ready"


async def test_unauthenticated_mcp_is_checked_without_starting_authorization(composio_setup, credential_protector):
    from a13n_service.connectivity.connections.checks import ConnectionChecks

    _, _, _, sessions, _, state, now = composio_setup
    principal = await app_actor(sessions)
    common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
    async with service_bundle(sessions, credential_protector) as (mcp, _, remote):
        remote.allow_anonymous = True
        connection = await common.create(
            actor=principal,
            workspace_id=WORKSPACE_ID,
            idempotency_key="noauth-create",
            request=CreateConnectionRequest.model_validate(
                {"name": "Public tools", "source": {"kind": "mcp", "endpoint_url": MCP_ENDPOINT, "auth_mode": "none"}}
            ),
        )
        assert connection.status == "pending" and remote.requests == []
        checks = ConnectionChecks(sessions, state["registry"], credential_protector, mcp, clock=lambda: now[0])
        checked = await checks.check(actor=principal, connection_id=connection.id, expected_version=connection.version)
        assert checked.status == "ready"
        assert checked.last_check.scope == "mcp_discovery" and checked.last_check.status == "passed"
        assert checked.authorization_generation == connection.authorization_generation


async def test_connection_check_does_not_restore_previous_account_during_authorization(
    composio_setup, credential_protector
):
    from a13n_service.connectivity.connections.checks import ConnectionChecks

    connector, connection, _, sessions, _, state, now = composio_setup
    principal = await app_actor(sessions)
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="pending-check",
            request=browser_request(connection.version, connector=True),
        )
        common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
        current = await common.get(actor=principal, connection_id=connection.id)
        checks = ConnectionChecks(sessions, state["registry"], credential_protector, mcp, clock=lambda: now[0])
        with pytest.raises(ConnectionError, match="Complete authorization"):
            await checks.check(actor=principal, connection_id=connection.id, expected_version=current.version)
        assert (await common.get(actor=principal, connection_id=connection.id)).status == "pending"


async def test_mcp_preparation_failure_is_queryable_and_never_repeats_registration(
    composio_setup, credential_protector, monkeypatch
):
    from a13n_service.connectivity.mcp.oauth_client import MCPOAuthError

    connector, _, _, sessions, _, _, now = composio_setup
    principal = await app_actor(sessions)
    common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        connection = await common.create(
            actor=principal,
            workspace_id=WORKSPACE_ID,
            idempotency_key="prepare-create",
            request=CreateConnectionRequest.model_validate(
                {
                    "name": "Preparation failure",
                    "source": {"kind": "mcp", "endpoint_url": MCP_ENDPOINT, "auth_mode": "oauth"},
                }
            ),
        )
        calls = 0

        async def unavailable(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise MCPOAuthError("metadata_unavailable")

        monkeypatch.setattr(oauth._oauth, "prepare", unavailable)
        request = browser_request(connection.version)
        result = await service.create(
            actor=principal, connection_id=connection.id, idempotency_key="prepare-auth", request=request
        )
        assert result.status == "failed" and result.error_code == "metadata_unavailable"
        assert (await service.get(actor=principal, authorization_id=result.id)) == result
        assert (
            await service.create(
                actor=principal, connection_id=connection.id, idempotency_key="prepare-auth", request=request
            )
        ) == result
        assert calls == 1


async def test_mcp_preparation_cancellation_fences_a_late_provider_response(
    composio_setup, credential_protector, monkeypatch
):
    from asyncio import create_task

    from a13n_service.connectivity.mcp.errors import MCPConnectionError
    from anyio import Event, fail_after

    connector, _, _, sessions, _, _, now = composio_setup
    principal = await app_actor(sessions)
    common = ConnectionService(sessions, EndpointPolicy(), clock=lambda: now[0])
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        connection = await common.create(
            actor=principal,
            workspace_id=WORKSPACE_ID,
            idempotency_key="cancel-create",
            request=CreateConnectionRequest.model_validate(
                {
                    "name": "Cancelled preparation",
                    "source": {"kind": "mcp", "endpoint_url": MCP_ENDPOINT, "auth_mode": "oauth"},
                }
            ),
        )
        entered, release = Event(), Event()
        original = oauth._oauth.prepare

        async def delayed(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(oauth._oauth, "prepare", delayed)
        request = browser_request(connection.version)
        with fail_after(5):
            pending = create_task(
                service.create(
                    actor=principal, connection_id=connection.id, idempotency_key="cancel-auth", request=request
                )
            )
            await entered.wait()
            replay = await service.create(
                actor=principal, connection_id=connection.id, idempotency_key="cancel-auth", request=request
            )
            assert replay.status == "preparing"
            await service.cancel(actor=principal, authorization_id=replay.id)
            release.set()
            with pytest.raises(MCPConnectionError):
                await pending
        assert (await service.get(actor=principal, authorization_id=replay.id)).status == "cancelled"
        current = await common.get(actor=principal, connection_id=connection.id)
        assert current.status == "pending" and current.authorization_generation == connection.authorization_generation


async def test_completion_rechecks_application_permissions(composio_setup, credential_protector):
    connector, connection, _, sessions, _, state, now = composio_setup
    principal = await app_actor(sessions)
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        auth = await service.create(
            actor=principal,
            connection_id=connection.id,
            idempotency_key="revoked-app",
            request=browser_request(connection.version, connector=True),
        )
        proof = await browser_roundtrip(service, auth, session_uri="opaque-provider-session")
        async with transaction(sessions) as session:
            binding = await session.get(RoleBindingRecord, "rb_connectivity_runner")
            binding.role_key = "runner"
        with pytest.raises(ConnectionError):
            await service.complete(actor=principal, authorization_id=auth.id, request=proof)
        assert state["redeem_calls"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://customer.example/oauth/complete/extra",
        "https://customer.example.evil.test/oauth/complete",
        "https://customer.example/oauth/complete?next=evil",
    ],
)
async def test_return_url_registration_is_exact_before_provider_io(composio_setup, credential_protector, url):
    connector, connection, _, sessions, _, state, now = composio_setup
    principal = await app_actor(sessions)
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        service = AuthorizationService(
            sessions,
            credential_protector,
            connector,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=(RETURN_URL,),
            clock=lambda: now[0],
        )
        with pytest.raises(ConnectionError, match="not registered"):
            await service.create(
                actor=principal,
                connection_id=connection.id,
                idempotency_key="invalid-return",
                request=browser_request(connection.version, connector=True).model_copy(update={"return_url": url}),
            )
        assert state["link_calls"] == 0
