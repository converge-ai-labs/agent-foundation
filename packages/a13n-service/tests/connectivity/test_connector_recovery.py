"""Exercise setup ownership and final dispatch authorization across external I/O."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import httpx2
import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.domain import CreateConnectorConnectionRequest, CreateConnectorProviderRequest
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorProviderRecord,
    ConnectorSetupAttemptRecord,
)
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.execution import AttemptToolScope
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.connectivity.selection_domain import ConnectorConnectionRunSelection
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, FrozenRunConnectivity
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.storage import short_session, transaction
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor
from .connector_helpers import FakeConnectorProvider
from .test_connector_service import connector_backend as connector_backend
from .test_connector_service import connector_registry as connector_registry
from .test_connector_service import connector_services as connector_services
from .test_connector_service import create_connection, create_connector
from .test_openconnector_catalog import AllowEndpoint
from .test_openconnector_project import project_server as project_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def recovery_sessions(request):
    return request.getfixturevalue(getattr(request, "param", "connectivity_sessions"))


@pytest.fixture
async def managed_project(project_server, recovery_sessions, credential_protector):
    connectivity_sessions = recovery_sessions
    state, requests, respond = project_server
    state["use_request_owner"] = True
    now = [NOW]

    async def response(request):
        result = respond(request)
        hook = state.get("response_hook")
        return await hook(request, result) if hook is not None else result

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as http:
        registry = built_in_connector_provider_registry(http, AllowEndpoint(), response_max_bytes=1024 * 1024)
        providers = ConnectorProviderService(
            connectivity_sessions, registry, credential_protector, clock=lambda: now[0]
        )
        connections = ConnectorConnectionService(
            connectivity_sessions,
            registry,
            credential_protector,
            correlation_secret=b"c" * 32,
            public_origin=None,
            setup_ttl_seconds=600,
            setup_lease_seconds=60,
            clock=lambda: now[0],
        )
        provider = await providers.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="provider",
            request=CreateConnectorProviderRequest(
                name="OpenConnector",
                type="openconnector",
                configuration={"enabled_services": ["github"]},
                credentials={"project_api_key": "project-secret", "catalog_api_key": "catalog-secret"},
            ),
        )
        connection = await create_connection(
            connections, connector_provider_id=provider.id, idempotency_key="connection"
        )
        reconciler = ConnectorReconciler(
            connectivity_sessions,
            registry,
            connections.setup_coordinator,
            instance_id="control",
            poll_interval_seconds=2,
            lease_seconds=60,
            clock=lambda: now[0],
        )
        yield connections, connection, reconciler, registry, state, requests, now


async def launch(connections, connection):
    return await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        expected_version=connection.version,
        idempotency_key="setup",
        setup={},
        return_path="/connections",
    )


@pytest.mark.parametrize(
    "recovery_sessions", ["connectivity_sessions", "postgres_connectivity_sessions"], indirect=True
)
async def test_pending_setup_has_one_sender_across_http_replay_and_reconciliation(managed_project, recovery_sessions):
    connectivity_sessions = recovery_sessions
    connections, connection, reconciler, _, state, requests, _ = managed_project
    started, release = asyncio.Event(), asyncio.Event()

    async def block_link(request, response):
        if request.url.path.endswith("/link"):
            started.set()
            await release.wait()
        return response

    state["response_hook"] = block_link
    first = asyncio.create_task(launch(connections, connection))
    try:
        await asyncio.wait_for(started.wait(), 5)
        replay = await launch(connections, connection)
        assert replay.status == "pending" and replay.redirect_url is None
        assert not await reconciler.reconcile_once()
        async with short_session(connectivity_sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, replay.attempt_id)
            assert attempt.status == "starting" and attempt.claim_owner is not None
    finally:
        release.set()
        result = await first
    assert replay.attempt_id == result.attempt_id
    assert result.redirect_url is not None
    assert await launch(connections, connection) == result
    assert sum(r.url.path.endswith("/link") for r in requests) == 1


@pytest.mark.parametrize("crash", [False, True])
async def test_interrupted_non_idempotent_setup_is_never_resent(
    managed_project, connectivity_sessions, monkeypatch, crash
):
    connections, connection, reconciler, _, state, requests, now = managed_project
    started = asyncio.Event()

    async def lose_response(request, response):
        if request.url.path.endswith("/link"):
            started.set()
            await asyncio.Event().wait()
        return response

    async def no_cleanup(*args, **kwargs):
        pass

    state["response_hook"] = lose_response
    coordinator = connections.setup_coordinator
    release = coordinator.release_attempt
    if crash:
        # Preserve the durable lease as if the process died without running finally.
        monkeypatch.setattr(coordinator, "release_attempt", no_cleanup)
    task = asyncio.create_task(launch(connections, connection))
    try:
        await asyncio.wait_for(started.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    monkeypatch.setattr(coordinator, "release_attempt", release)
    if crash:
        assert not await reconciler.reconcile_once()
        now[0] += timedelta(seconds=61)
    assert await reconciler.reconcile_once()
    assert not await reconciler.reconcile_once()
    async with short_session(connectivity_sessions) as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
        assert attempt.status == "failed" and attempt.last_error_code == "setup_outcome_unknown"
        assert attempt.setup_ref is None
        assert (await session.get(ConnectorConnectionRecord, connection.id)).status == "action_required"
    assert (await launch(connections, connection)).status == "failed"
    assert sum(r.url.path.endswith("/link") for r in requests) == 1
    current = await connections.get(actor=actor(), connection_id=connection.id)
    receipt = await connections.delete(
        actor=actor(), connection_id=connection.id, expected_version=current.version, idempotency_key="delete"
    )
    assert receipt.remote_status != "not_required"


async def test_known_retryable_rejection_can_retry_after_backoff(managed_project, connectivity_sessions):
    connections, connection, reconciler, _, state, requests, now = managed_project

    async def rate_limit(request, response):
        return httpx2.Response(429, headers={"retry-after": "10"}) if request.url.path.endswith("/link") else response

    state["response_hook"] = rate_limit
    with pytest.raises(ConnectorError):
        await launch(connections, connection)
    assert not await reconciler.reconcile_once()
    replay = await launch(connections, connection)
    assert replay.status == "pending" and replay.redirect_url is None
    assert sum(r.url.path.endswith("/link") for r in requests) == 1
    now[0] += timedelta(seconds=11)
    assert await reconciler.reconcile_once()
    now[0] += timedelta(seconds=6)
    assert not await reconciler.reconcile_once()
    state.pop("response_hook")
    now[0] += timedelta(seconds=5)
    assert await reconciler.reconcile_once()
    async with short_session(connectivity_sessions) as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
        assert attempt.status == "attached" and attempt.setup_ref == "request-1"
    assert sum(r.url.path.endswith("/link") for r in requests) == 3


async def test_expired_start_owner_cannot_publish_a_late_response(managed_project, connectivity_sessions):
    connections, connection, reconciler, _, state, requests, now = managed_project
    started, release = asyncio.Event(), asyncio.Event()

    async def late_response(request, response):
        if request.url.path.endswith("/link"):
            started.set()
            await release.wait()
        return response

    state["response_hook"] = late_response
    first = asyncio.create_task(launch(connections, connection))
    try:
        await asyncio.wait_for(started.wait(), 5)
        now[0] += timedelta(seconds=61)
        assert await reconciler.reconcile_once()
    finally:
        release.set()
        with pytest.raises(ConnectorError, match="changed concurrently"):
            await first
    async with short_session(connectivity_sessions) as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
        assert attempt.status == "failed" and attempt.setup_ref is None
        assert attempt.last_error_code == "setup_outcome_unknown"
    assert sum(r.url.path.endswith("/link") for r in requests) == 1


async def test_idempotent_provider_recovers_interrupted_start(
    connector_services, connector_registry, connector_backend, connectivity_sessions, monkeypatch
):
    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="connection")
    started = asyncio.Event()
    original = FakeConnectorProvider.start_setup

    async def lose_first_response(self, **kwargs):
        result = await original(self, **kwargs)
        if self.backend.started == 1:
            started.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(FakeConnectorProvider, "start_setup", lose_first_response)
    task = asyncio.create_task(
        connections.start_setup(
            actor=actor(),
            connection_id=connection.id,
            expected_version=connection.version,
            idempotency_key="setup",
            setup={"scopes": ["read"]},
            return_path="/connections",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        connector_registry,
        connections.setup_coordinator,
        instance_id="control",
        poll_interval_seconds=2,
        lease_seconds=60,
        clock=lambda: NOW,
    )
    assert await reconciler.reconcile_once()
    assert connector_backend.started == 2 and len(connector_backend.external_accounts) == 1
    async with short_session(connectivity_sessions) as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
        assert attempt.status == "attached" and attempt.external_ref == "external-1"


@pytest.mark.parametrize("malformed", ["envelope", "status", "account_id", "profile"])
async def test_malformed_polling_result_fails_only_its_setup(managed_project, connectivity_sessions, malformed):
    connections, connection, reconciler, _, state, _, _ = managed_project
    await launch(connections, connection)
    state["status"] = "connected"

    async def corrupt(request, response):
        value = response.json()
        if "/connection-requests/" in request.url.path:
            if malformed == "envelope":
                value = []
            elif malformed == "status":
                value["data"].pop("status")
            elif malformed == "account_id":
                value["data"]["connectedAccountId"] = None
        elif request.url.path.endswith("/profile") and malformed == "profile":
            value["data"]["profile"] = None
        return httpx2.Response(200, json=value)

    state["response_hook"] = corrupt
    assert await reconciler.reconcile_once()
    assert not await reconciler.reconcile_once()
    async with short_session(connectivity_sessions) as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
        assert attempt.status == "failed" and attempt.last_error_code == "invalid_provider_response"
        assert (await session.get(ConnectorConnectionRecord, connection.id)).status == "action_required"
    state.pop("response_hook")
    healthy = await connections.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="healthy",
        request=CreateConnectorConnectionRequest(
            connector_provider_id=connection.connector_provider_id, name="Healthy", connector_key="github"
        ),
    )
    await launch(connections, healthy)
    assert await reconciler.reconcile_once()
    assert (await connections.get(actor=actor(), connection_id=healthy.id)).status.value == "ready"


@pytest.mark.parametrize("phase", ["/profile", "/v1/actions"])
@pytest.mark.parametrize("change", ["connection", "provider", "credential", "attempt"])
async def test_authority_change_during_preflight_blocks_action(
    managed_project, connectivity_sessions, external_runtime_factory, phase, change, execution_authorization
):
    connections, connection, reconciler, registry, state, requests, _ = managed_project
    await launch(connections, connection)
    state["status"] = "connected"
    assert await reconciler.reconcile_once()
    authorized = True

    async def guard(session=None):
        if not authorized:
            raise ValueError("attempt_no_longer_authorized")

    policy = EndpointPolicy()
    runtime = external_runtime_factory(registry, RemoteTransport(policy), policy)
    capability = await runtime._connector(
        ConnectorConnectionRunSelection(
            connector_connection_id=connection.id,
            connector_provider_id=connection.connector_provider_id,
            tools=("github.get_user",),
        ),
        guard,
        AttemptToolScope(
            replace(actor(), auth_method="internal"),
            ORG_ID,
            WORKSPACE_ID,
            FrozenRunConnectivity((), ()),
            (),
            authorization=await execution_authorization(),
        ),
    )

    async def invalidate(request, response):
        nonlocal authorized
        if request.url.path.endswith(phase):
            async with transaction(connectivity_sessions) as session:
                if change == "connection":
                    record = await session.get(ConnectorConnectionRecord, connection.id)
                    record.status = "disabled"
                    record.version += 1
                elif change in {"provider", "credential"}:
                    record = await session.get(ConnectorProviderRecord, connection.connector_provider_id)
                    if change == "provider":
                        record.status = "disabled"
                    else:
                        record.credential_generation += 1
                else:
                    authorized = False
        return response

    state["response_hook"] = invalidate
    requests.clear()
    with pytest.raises((ValueError, ConnectivitySelectionError)):
        await Agent(TestModel(), capabilities=[capability]).run("read user")
    assert not any(r.method == "POST" for r in requests)
