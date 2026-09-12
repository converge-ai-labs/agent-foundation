"""Composio's documented wire protocol, browser proof, and one-shot recovery."""

import asyncio
import json
from datetime import timedelta

import httpx2
import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import ConnectorAuthorizationRecord, ConnectorConnectionRecord
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.storage import short_session
from sqlalchemy import select

from .conftest import NOW, WORKSPACE_ID, actor
from .connection_helpers import management
from .connector_helpers import AllowEndpoint
from .test_connector_service import create_connection

pytestmark = pytest.mark.anyio
NONCE = "b" * 64


@pytest.fixture
def composio_sessions(request):
    return request.getfixturevalue(getattr(request, "param", "connectivity_sessions"))


@pytest.fixture
async def composio_setup(composio_sessions, credential_protector):
    sessions = composio_sessions
    now = [NOW]
    requests = []
    state = {"status": "INITIALIZING", "user_id": "", "account_id": "ca_test", "link_calls": 0, "redeem_calls": 0}

    async def respond(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/toolkits") or path.endswith("/toolkits/github"):
            toolkit = {
                "slug": "github",
                "name": "GitHub",
                "meta": {"version": "20260903_01"},
                "auth_config_details": [
                    {
                        "mode": state.get("scheme", "OAUTH2"),
                        "fields": {
                            "connected_account_initiation": {
                                "required": [],
                                "optional": [{"name": "subdomain", "type": "string"}],
                            }
                        },
                    }
                ],
            }
            return httpx2.Response(200, json={"items": [toolkit]} if path.endswith("/toolkits") else toolkit)
        if path.endswith("/tools"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "slug": "GITHUB_GET_USER",
                            "toolkit": {"slug": "github"},
                            "version": "20260903_01",
                            "description": "Read current user",
                            "input_parameters": {"type": "object", "properties": {}},
                            "output_parameters": {"type": "object"},
                        }
                    ]
                },
            )
        if path.endswith("/auth_configs"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "ac_test",
                            "toolkit": {"slug": "github"},
                            "status": "ENABLED",
                            "auth_scheme": state.get("scheme", "OAUTH2"),
                        }
                    ]
                },
            )
        if path.endswith("/link"):
            state["link_calls"] += 1
            state["user_id"] = json.loads(request.content)["user_id"]
            async with short_session(sessions) as session:
                attempt = await session.scalar(
                    select(ConnectorAuthorizationRecord).order_by(ConnectorAuthorizationRecord.generation.desc())
                )
                assert attempt.status == "starting" and attempt.claim_owner and attempt.claim_generation >= 1
                assert attempt.browser_binding_digest or attempt.launch_token_digest
            if state.get("lose_link"):
                raise httpx2.ReadTimeout("response lost")
            return httpx2.Response(
                201,
                json={
                    "connected_account_id": "ca_test",
                    "redirect_url": "https://connect.composio.dev/link/test",
                    "link_token": "unused",
                    "expires_at": (now[0] + timedelta(seconds=120)).isoformat(),
                },
            )
        if path.endswith("/complete_auth"):
            state["redeem_calls"] += 1
            assert json.loads(request.content)["user_id"] == state["user_id"]
            if state.get("entered"):
                state["entered"].set()
                await state["release"].wait()
            state["status"] = "ACTIVE"
            if state.get("lose_redemption"):
                raise httpx2.ReadTimeout("response lost")
            return httpx2.Response(200, json={"connected_account_id": state["account_id"], "toolkit_slug": "github"})
        assert path.endswith("/connected_accounts/ca_test"), path
        return httpx2.Response(
            200,
            json={
                "id": "ca_test",
                "user_id": state["user_id"],
                "toolkit": {"slug": "github"},
                "status": state["status"],
                "state": {"access_token": "must-not-escape"},
            },
        )

    async def response(request):
        result = await respond(request)
        hook = state.get("response_hook")
        return await hook(request, result) if hook is not None else result

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as http:
        registry = built_in_connector_provider_registry(http, AllowEndpoint(), response_max_bytes=1024 * 1024)
        state["registry"] = registry
        providers = ConnectorProviderService(sessions, registry, credential_protector, clock=lambda: now[0])
        service = ConnectorConnectionService(
            sessions,
            registry,
            credential_protector,
            correlation_secret=b"c" * 32,
            public_origin="https://foundation.example",
            setup_ttl_seconds=600,
            clock=lambda: now[0],
        )
        provider = await providers.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="provider",
            request=CreateConnectorProviderRequest(
                name="Composio",
                type="composio",
                configuration={},
                credentials={"api_key": "secret"},
            ),
        )
        connection = await create_connection(service, connector_provider_id=provider.id, idempotency_key="connection")
        reconciler = ConnectorReconciler(
            sessions,
            registry,
            service.setup_coordinator,
            instance_id="control",
            poll_interval_seconds=2,
            lease_seconds=60,
            clock=lambda: now[0],
        )
        yield service, connection, reconciler, sessions, requests, state, now


async def launch(service, connection, *, nonce=NONCE):
    return await service.start_setup(
        actor=actor(),
        connection_id=connection.id,
        expected_version=connection.version,
        idempotency_key="setup",
        setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
        return_url="/connections",
        browser_nonce=nonce,
    )


async def complete(service, attempt_id, *, nonce=NONCE):
    return await service.complete_callback(
        actor=actor(), attempt_id=attempt_id, browser_nonce=nonce, session_uri="opaque-single-use-session"
    )


async def test_link_attaches_only_attempt_and_replay_does_not_create_an_account(composio_setup):
    service, connection, reconciler, sessions, requests, state, now = composio_setup
    result = await launch(service, connection)
    assert result.completion_method == "oauth_verifier" and result.expires_at == now[0] + timedelta(seconds=120)
    async with short_session(sessions) as session:
        record = await session.get(ConnectorConnectionRecord, connection.id)
        attempt = await session.get(ConnectorAuthorizationRecord, result.attempt_id)
        assert record.external_ref is None and attempt.external_ref == "ca_test"
        assert attempt.claim_owner is None and attempt.status == "attached"
        assert attempt.completion_method == "oauth_verifier"
    replay = await launch(service, connection)
    assert replay.attempt_id == result.attempt_id and replay.redirect_url is None
    assert state["link_calls"] == 1 and not await reconciler.reconcile_once()
    assert await complete(service, result.attempt_id) == "/connections"
    assert await complete(service, result.attempt_id) == "/connections"
    assert state["redeem_calls"] == 1
    assert requests[-1].method == "GET" and requests[-1].url.path.endswith("/ca_test")
    ready = await management(service).get(actor=actor(), connection_id=connection.id)
    assert ready.status == "ready" and "must-not-escape" not in repr(ready)


@pytest.mark.parametrize("nonce", [None, "short", "g" * 64])
async def test_browser_proof_is_required_before_creating_remote_accounts(composio_setup, nonce):
    service, connection, _, sessions, _, state, _ = composio_setup
    with pytest.raises(ConnectorError):
        await launch(service, connection, nonce=nonce)
    assert state["link_calls"] == 0
    async with short_session(sessions) as session:
        assert await session.scalar(select(ConnectorAuthorizationRecord)) is None


async def test_wrong_tab_and_idempotency_binding_cannot_redeem(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    result = await launch(service, connection)
    with pytest.raises(ConnectorError, match="browser authorization context"):
        await complete(service, result.attempt_id, nonce="a" * 64)
    with pytest.raises(ConnectorError):
        await launch(service, connection, nonce="a" * 64)
    assert state["redeem_calls"] == 0 and state["link_calls"] == 1
    assert await complete(service, result.attempt_id) == "/connections"


async def test_lost_initial_response_is_not_replayed(composio_setup):
    service, connection, reconciler, sessions, _, state, now = composio_setup
    state["lose_link"] = True
    with pytest.raises(ConnectorError):
        await launch(service, connection)
    now[0] += timedelta(seconds=61)
    assert not await reconciler.reconcile_once()
    assert (await launch(service, connection)).status == "failed"
    async with short_session(sessions) as session:
        attempt = await session.scalar(select(ConnectorAuthorizationRecord))
        assert attempt.last_error_code == "setup_outcome_unknown"
    assert state["link_calls"] == 1


async def test_lost_redemption_response_recovers_by_exact_account_read(composio_setup):
    service, connection, reconciler, sessions, requests, state, now = composio_setup
    result = await launch(service, connection)
    state["lose_redemption"] = True
    with pytest.raises(ConnectorError):
        await complete(service, result.attempt_id)
    with pytest.raises(ConnectorError):
        await complete(service, result.attempt_id)
    async with short_session(sessions) as session:
        attempt = await session.get(ConnectorAuthorizationRecord, result.attempt_id)
        assert attempt.status == "reserved" and attempt.last_error_code == "provider_unavailable"
    now[0] += timedelta(seconds=6)
    assert await reconciler.reconcile_once()
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "ready"
    assert state["redeem_calls"] == 1 and state["link_calls"] == 1
    assert requests[-1].method == "GET"


@pytest.mark.parametrize(
    "composio_sessions", ["connectivity_sessions", "postgres_connectivity_sessions"], indirect=True
)
async def test_concurrent_callbacks_have_one_redeemer(composio_setup):
    service, connection, reconciler, _, _, state, _ = composio_setup
    result = await launch(service, connection)
    state["entered"], state["release"] = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(complete(service, result.attempt_id))
    try:
        await asyncio.wait_for(state["entered"].wait(), 5)
        with pytest.raises(ConnectorError) as busy:
            await complete(service, result.attempt_id)
        assert busy.value.code == "setup_in_progress"
        assert not await reconciler.reconcile_once()
    finally:
        state["release"].set()
        assert await first == "/connections"
    assert state["redeem_calls"] == 1


async def test_returned_other_account_is_never_bound_or_recovered(composio_setup):
    service, connection, reconciler, sessions, _, state, now = composio_setup
    result = await launch(service, connection)
    state["account_id"] = "ca_other"
    with pytest.raises(ConnectorError):
        await complete(service, result.attempt_id)
    now[0] += timedelta(seconds=6)
    assert not await reconciler.reconcile_once()
    async with short_session(sessions) as session:
        attempt = await session.get(ConnectorAuthorizationRecord, result.attempt_id)
        record = await session.get(ConnectorConnectionRecord, connection.id)
        assert attempt.status == "failed" and attempt.last_error_code == "connection_substitution"
        assert record.external_ref is None and record.status == "action_required"


async def test_reauthorization_replaces_upstream_account_under_the_same_connection(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    first = await launch(service, connection)
    await complete(service, first.attempt_id)
    current = await management(service).get(actor=actor(), connection_id=connection.id)
    replacement = await service.start_setup(
        actor=actor(),
        connection_id=connection.id,
        expected_version=current.version,
        idempotency_key="reauthorize",
        setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
        return_url="/connections",
        browser_nonce=NONCE,
    )
    assert replacement.connection.id == current.id
    assert replacement.connection.status == "pending"
    await complete(service, replacement.attempt_id)
    replaced = await management(service).get(actor=actor(), connection_id=current.id)
    assert replaced.authorization_generation > current.authorization_generation
    assert replacement.attempt_id != first.attempt_id
    assert state["link_calls"] == 2


async def test_crash_after_reservation_before_redemption_never_replays_session(composio_setup, monkeypatch):
    service, connection, reconciler, sessions, _, state, now = composio_setup
    result = await launch(service, connection)
    entered = asyncio.Event()

    async def stop_before_send(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service.setup_coordinator, "_complete_attempt", stop_before_send)
    task = asyncio.create_task(complete(service, result.attempt_id))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await reconciler.reconcile_once()
    assert state["redeem_calls"] == 0
    async with short_session(sessions) as session:
        attempt = await session.get(ConnectorAuthorizationRecord, result.attempt_id)
        assert attempt.status == "reserved"
    now[0] += timedelta(seconds=121)
    assert await reconciler.reconcile_once()
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "action_required"


async def test_late_redemption_owner_cannot_publish_after_lease_expires(composio_setup):
    service, connection, reconciler, sessions, _, state, now = composio_setup
    result = await launch(service, connection)
    state["entered"], state["release"] = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(complete(service, result.attempt_id))
    try:
        await asyncio.wait_for(state["entered"].wait(), 5)
        now[0] += timedelta(seconds=61)
        assert await reconciler.reconcile_once()
    finally:
        state["release"].set()
        with pytest.raises(ConnectorError):
            await first
    async with short_session(sessions) as session:
        assert (await session.get(ConnectorConnectionRecord, connection.id)).external_ref is None
    now[0] += timedelta(seconds=6)
    assert await reconciler.reconcile_once()
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "ready"
    assert state["redeem_calls"] == 1


@pytest.mark.parametrize(
    "composio_sessions", ["connectivity_sessions", "postgres_connectivity_sessions"], indirect=True
)
async def test_new_idempotency_key_cannot_start_same_setup_generation(composio_setup):
    service, connection, _, sessions, _, state, _ = composio_setup
    first = await launch(service, connection)
    for nonce in (NONCE, "c" * 64):
        with pytest.raises(ConnectorError) as raised:
            await service.start_setup(
                actor=actor(),
                connection_id=connection.id,
                expected_version=connection.version,
                idempotency_key=f"different-{nonce[0]}",
                setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
                return_url="/connections",
                browser_nonce=nonce,
            )
        assert raised.value.code == "version_conflict"
    assert state["link_calls"] == 1
    assert (await launch(service, connection)).attempt_id == first.attempt_id
    async with short_session(sessions) as session:
        attempts = list(await session.scalars(select(ConnectorAuthorizationRecord)))
        assert len(attempts) == 1
    restarted = await service.start_setup(
        actor=actor(),
        connection_id=connection.id,
        expected_version=first.connection.version,
        idempotency_key="explicit-restart",
        setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
        return_url="/connections",
        browser_nonce=NONCE,
    )
    assert restarted.attempt_id != first.attempt_id and state["link_calls"] == 2


@pytest.mark.parametrize("scheme", ["API_KEY", "BEARER_TOKEN", "BASIC"])
async def test_non_oauth_requires_bound_confirmation_before_attachment(composio_setup, scheme):
    service, connection, reconciler, _, requests, state, _ = composio_setup
    state["scheme"] = scheme
    result = await launch(service, connection)
    assert result.completion_method == "browser_confirmation"
    link = next(json.loads(request.content) for request in requests if request.url.path.endswith("/link"))
    assert "connection_data" not in link and "?" not in link["callback_url"]
    state["status"] = "ACTIVE"
    assert not await reconciler.reconcile_once()
    with pytest.raises(ConnectorError):
        await service.complete_callback(
            actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE, session_uri="oauth-downgrade"
        )
    with pytest.raises(ConnectorError):
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce="a" * 64)
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "pending"
    assert (
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
        == "/connections"
    )
    assert state["redeem_calls"] == 0
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "ready"


async def test_oauth_cannot_be_completed_by_confirmation(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    result = await launch(service, connection)
    state["status"] = "ACTIVE"
    with pytest.raises(ConnectorError, match="OAuth user verification"):
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status == "pending"


async def test_non_oauth_confirmation_rejects_an_account_with_changed_owner(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    state["scheme"] = "API_KEY"
    result = await launch(service, connection)
    state["status"] = "ACTIVE"
    state["user_id"] = "another-owner"
    with pytest.raises(ConnectorError):
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
    assert (await management(service).get(actor=actor(), connection_id=connection.id)).status != "ready"
    assert state["redeem_calls"] == 0


@pytest.mark.parametrize("scheme", ["OAUTH2", "API_KEY", "BEARER_TOKEN", "BASIC"])
async def test_http_api_creates_and_authorizes_composio_with_optional_prefill(
    composio_setup, credential_protector, monkeypatch, scheme
):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit

    from a13n_service.api import install_api_conventions
    from a13n_service.connectivity.connections import router
    from a13n_service.connectivity.connections.authorization import AuthorizationService
    from a13n_service.connectivity.connections.handoff import digest
    from a13n_service.iam import authenticate_request
    from a13n_service.iam.http.authentication import authenticate_mutation
    from a13n_service.iam.http.resource_dependencies import resolve_workspace
    from fastapi import FastAPI

    from .test_connection_authorizations import app_actor
    from .test_mcp_service import service_bundle

    service, existing, _, sessions, requests, state, now = composio_setup
    state["scheme"] = scheme
    principal = await app_actor(sessions)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router.router)
    app.dependency_overrides[authenticate_request] = lambda: principal
    app.dependency_overrides[authenticate_mutation] = lambda: principal
    app.dependency_overrides[resolve_workspace] = lambda: WORKSPACE_ID
    async with service_bundle(sessions, credential_protector) as (mcp, oauth, _):
        authorizations = AuthorizationService(
            sessions,
            credential_protector,
            service,
            oauth,
            mcp,
            public_origin="https://foundation.example",
            return_urls=("https://customer.example/callback",),
            clock=lambda: now[0],
        )
        runtime = SimpleNamespace(connections=management(service), authorizations=authorizations)
        monkeypatch.setattr(router, "_runtime", lambda request: runtime)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="https://foundation.example"
        ) as client:
            created = await client.post(
                f"/api/v1/workspaces/{WORKSPACE_ID}/connections",
                headers={"Idempotency-Key": "http-create"},
                json={
                    "source": {
                        "kind": "connector",
                        "provider_id": existing.source.provider_id,
                        "connector_key": "github",
                    },
                    "name": "API-created account",
                },
            )
            assert created.status_code == 201, created.text
            connection = created.json()
            assert state["link_calls"] == 0
            path = f"/api/v1/connections/{connection['id']}"
            started = await client.post(
                path + "/authorizations",
                headers={"Idempotency-Key": "http-start"},
                json={
                    "expected_version": connection["version"],
                    "method": "browser",
                    "return_url": "https://customer.example/callback",
                    "state": "s" * 32,
                    "completion_challenge": digest("v" * 64),
                    "options": {
                        "auth_config_id": "ac_test",
                        "toolkit_version": "20260903_01",
                        "connection_data": {"subdomain": "team"},
                    },
                },
            )
            assert started.status_code == 201, started.text
            authorization = started.json()
            assert authorization["connection_id"] == connection["id"]
            launch_token = parse_qs(urlsplit(authorization["next_action"]["url"]).fragment)["token"][0]
            auth_path = f"/api/v1/connection-authorizations/{authorization['id']}"
            launched = await client.post(auth_path + "/launch", json={"token": launch_token, "browser_nonce": NONCE})
            assert launched.status_code == 200, launched.text
            assert launched.json()["url"] == "https://connect.composio.dev/link/test"
            upstream = next(request for request in requests if request.url.path.endswith("/link"))
            assert json.loads(upstream.content)["connection_data"] == {"subdomain": "team"}
            state["status"] = "ACTIVE"
            received = await client.post(
                auth_path + "/receive",
                json={
                    "browser_nonce": NONCE,
                    **({"session_uri": "opaque-single-use-session"} if scheme == "OAUTH2" else {}),
                },
            )
            assert received.status_code == 200, received.text
            receipt = parse_qs(urlsplit(received.json()["url"]).query)["receipt"][0]
            completed = await client.post(
                auth_path + "/complete", json={"receipt": receipt, "completion_verifier": "v" * 64}
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "completed"
            current = (await client.get(path)).json()
            assert current["status"] == "ready" and current["id"] == connection["id"]
