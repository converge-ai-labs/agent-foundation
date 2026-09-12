"""Composio's documented wire protocol, browser proof, and one-shot recovery."""

import asyncio
import json
from datetime import timedelta

import httpx2
import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorSetupAttemptRecord
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from .conftest import NOW, WORKSPACE_ID, actor
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
                    select(ConnectorSetupAttemptRecord).order_by(ConnectorSetupAttemptRecord.generation.desc())
                )
                assert attempt.status == "starting" and attempt.claim_owner and attempt.claim_generation >= 1
                assert attempt.browser_binding_digest and NONCE not in attempt.browser_binding_digest
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
        return_path="/connections",
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
        attempt = await session.get(ConnectorSetupAttemptRecord, result.attempt_id)
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
    ready = await service.get(actor=actor(), connection_id=connection.id)
    assert ready.status == "ready" and "must-not-escape" not in repr(ready)


@pytest.mark.parametrize("nonce", [None, "short", "g" * 64])
async def test_browser_proof_is_required_before_creating_remote_accounts(composio_setup, nonce):
    service, connection, _, sessions, _, state, _ = composio_setup
    with pytest.raises(ConnectorError):
        await launch(service, connection, nonce=nonce)
    assert state["link_calls"] == 0
    async with short_session(sessions) as session:
        assert await session.scalar(select(ConnectorSetupAttemptRecord)) is None


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
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
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
        attempt = await session.get(ConnectorSetupAttemptRecord, result.attempt_id)
        assert attempt.status == "reserved" and attempt.last_error_code == "provider_unavailable"
    now[0] += timedelta(seconds=6)
    assert await reconciler.reconcile_once()
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "ready"
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
        attempt = await session.get(ConnectorSetupAttemptRecord, result.attempt_id)
        record = await session.get(ConnectorConnectionRecord, connection.id)
        assert attempt.status == "failed" and attempt.last_error_code == "connection_substitution"
        assert record.external_ref is None and record.status == "action_required"


async def test_reconnect_of_verified_account_is_rejected_before_remote_write(composio_setup):
    service, connection, _, sessions, _, state, _ = composio_setup
    result = await launch(service, connection)
    await complete(service, result.attempt_id)
    async with transaction(sessions) as session:
        record = await session.get(ConnectorConnectionRecord, connection.id)
        record.status = "action_required"
        record.status_reason = "reauthorization_required"
    current = await service.get(actor=actor(), connection_id=connection.id)
    with pytest.raises(ConnectorError) as unsupported:
        await service.reconnect(
            actor=actor(),
            connection_id=connection.id,
            expected_version=current.version,
            idempotency_key="reconnect",
            setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
            return_path="/connections",
            browser_nonce=NONCE,
        )
    assert unsupported.value.code == "reconnect_unsupported"
    assert state["link_calls"] == 1


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
        attempt = await session.get(ConnectorSetupAttemptRecord, result.attempt_id)
        assert attempt.status == "reserved"
    now[0] += timedelta(seconds=121)
    assert await reconciler.reconcile_once()
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "action_required"


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
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "ready"
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
                return_path="/connections",
                browser_nonce=nonce,
            )
        assert raised.value.code == "setup_already_started"
    assert state["link_calls"] == 1
    assert (await launch(service, connection)).attempt_id == first.attempt_id
    async with short_session(sessions) as session:
        attempts = list(await session.scalars(select(ConnectorSetupAttemptRecord)))
        assert len(attempts) == 1
    restarted = await service.reconnect(
        actor=actor(),
        connection_id=connection.id,
        expected_version=connection.version,
        idempotency_key="explicit-restart",
        setup={"auth_config_id": "ac_test", "toolkit_version": "20260903_01"},
        return_path="/connections",
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
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "pending"
    assert (
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
        == "/connections"
    )
    assert state["redeem_calls"] == 0
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "ready"


async def test_oauth_cannot_be_completed_by_confirmation(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    result = await launch(service, connection)
    state["status"] = "ACTIVE"
    with pytest.raises(ConnectorError, match="OAuth user verification"):
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
    assert (await service.get(actor=actor(), connection_id=connection.id)).status == "pending"


async def test_non_oauth_confirmation_rejects_an_account_with_changed_owner(composio_setup):
    service, connection, _, _, _, state, _ = composio_setup
    state["scheme"] = "API_KEY"
    result = await launch(service, connection)
    state["status"] = "ACTIVE"
    state["user_id"] = "another-owner"
    with pytest.raises(ConnectorError):
        await service.complete_callback(actor=actor(), attempt_id=result.attempt_id, browser_nonce=NONCE)
    assert (await service.get(actor=actor(), connection_id=connection.id)).status != "ready"
    assert state["redeem_calls"] == 0


@pytest.mark.parametrize("scheme", ["OAUTH2", "API_KEY", "BEARER_TOKEN", "BASIC"])
async def test_http_api_creates_and_authorizes_composio_with_optional_prefill(composio_setup, monkeypatch, scheme):
    from dataclasses import replace

    from a13n_service.api import install_api_conventions
    from a13n_service.connectivity.connectors import router
    from a13n_service.iam import authenticate_request
    from a13n_service.iam.http.resource_dependencies import resolve_workspace
    from fastapi import FastAPI

    service, existing, _, _, requests, state, _ = composio_setup
    state["scheme"] = scheme
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router.router)
    # Exercise the HTTP contract and real service using an already authenticated User API principal.
    app.dependency_overrides[authenticate_request] = lambda: replace(actor(), auth_method="api_key")
    app.dependency_overrides[resolve_workspace] = lambda: WORKSPACE_ID
    monkeypatch.setattr(router, "_connections", lambda request: service)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="https://foundation.example") as client:
        created = await client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/connector-connections",
            headers={"Idempotency-Key": "http-create"},
            json={
                "connector_provider_id": existing.connector_provider_id,
                "connector_key": "github",
                "name": "API-created account",
            },
        )
        assert created.status_code == 201, created.text
        connection = created.json()
        path = f"/api/v1/connector-connections/{connection['id']}"
        started = await client.post(
            path + "/setup",
            headers={"Idempotency-Key": "http-start"},
            json={
                "expected_version": connection["version"],
                "browser_nonce": NONCE,
                "return_path": "/connections",
                "setup": {
                    "auth_config_id": "ac_test",
                    "toolkit_version": "20260903_01",
                    "connection_data": {"subdomain": "team"},
                },
            },
        )
        assert started.status_code == 200, started.text
        launch = started.json()
        assert launch["redirect_url"] == "https://connect.composio.dev/link/test"
        assert launch["completion_method"] == ("oauth_verifier" if scheme == "OAUTH2" else "browser_confirmation")
        upstream = next(request for request in requests if request.url.path.endswith("/link"))
        assert json.loads(upstream.content)["connection_data"] == {"subdomain": "team"}
        state["status"] = "ACTIVE"
        completed = await client.post(
            "/api/v1/connector-setup/complete",
            json={
                "attempt_id": launch["attempt_id"],
                "browser_nonce": NONCE,
                **({"session_uri": "opaque-single-use-session"} if scheme == "OAUTH2" else {}),
            },
        )
        assert completed.status_code == 200, completed.text
        assert (await client.get(path)).json()["status"] == "ready"
