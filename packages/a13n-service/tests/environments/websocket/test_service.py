from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

import pytest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.websocket.authority import LeaseDeadline
from a13n_service.environments.websocket.coordination import ConnectionCoordination, CoordinationLimits, environment_key
from a13n_service.environments.websocket.reconciliation import ClientConnectionReconciler
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.environments.websocket.service import ClientConnectionService, websocket_origin
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session
from anyio import create_task_group, fail_after

from ..conftest import actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def client_service(environment_service, redis_client):
    return ClientConnectionService(
        ConnectionResources(environment_service),
        ConnectionCoordination(redis_client, limits=CoordinationLimits(lease_ms=5_000)),
        public_origin="wss://control.example.test:443/",
    )


async def connect(service, target):
    ticket = await service.issue_ticket(actor(), target.environment_id)
    admitted = await service.coordination.admit(
        target.organization_id, target.environment_id, ticket=ticket.ticket, owner_instance_id="control"
    )
    connection = admitted.value.connection
    await asyncio.sleep(service.coordination.limits.lease_ms / 1000 + 0.02)
    await service.coordination.promote(connection)
    await service.coordination.online(connection)
    return connection


async def test_ticket_does_not_connect_and_status_identifies_its_candidate(client_service, target):
    ticket = await client_service.issue_ticket(actor(), target.environment_id)
    assert ticket.ticket not in repr(ticket)
    assert ticket.websocket_url == f"wss://control.example.test:443/api/v1/environments/{target.environment_id}/connect"
    assert ticket.ticket not in ticket.websocket_url
    assert (await client_service.status(actor(), target.environment_id)).status == "offline"
    admitted = await client_service.coordination.admit(
        target.organization_id, target.environment_id, ticket=ticket.ticket, owner_instance_id="control"
    )
    status = await client_service.status(actor(), target.environment_id)
    assert status.status == "connecting"
    assert status.connection_id == ticket.connection_id
    assert set(status.model_dump()) == {"status", "connection_id", "observed_at", "error"}
    await client_service.coordination.abandon(admitted.value.connection, error="environment_initialization_failed")
    status = await client_service.status(actor(), target.environment_id)
    assert status.status == "offline" and status.connection_id is None
    assert status.error == "environment_initialization_failed"


async def test_dependency_failure_is_not_reported_as_offline(client_service, target, redis_client):
    await redis_client.lpush(environment_key(target.organization_id, target.environment_id), "invalid-state")
    with pytest.raises(EnvironmentManagementError) as error:
        await client_service.status(actor(), target.environment_id)
    assert error.value.code == "environment_coordination_unavailable"


async def test_online_reply_expired_in_transit_is_retried_and_never_exposed(client_service, target, monkeypatch):
    await connect(client_service, target)
    observed = await client_service.coordination.observe(target.organization_id, target.environment_id)
    expired = replace(observed, request_started_at=monotonic() - 10)
    calls = 0

    async def delayed(*args):
        nonlocal calls
        calls += 1
        return expired

    monkeypatch.setattr(client_service.coordination, "observe", delayed)
    with pytest.raises(EnvironmentManagementError) as error:
        await client_service.status(actor(), target.environment_id)
    assert error.value.code == "environment_coordination_unavailable"
    assert calls == 2


async def test_reconciliation_observes_online_and_recovers_owner_loss(client_service, environment_service, target):
    connection = await connect(client_service, target)
    reconciler = ClientConnectionReconciler(client_service)

    async def keep_online():
        while True:
            observed = await client_service.coordination.renew(connection)
            assert observed.value.connection == connection and observed.value.status == "online"
            await asyncio.sleep(client_service.coordination.limits.lease_ms / 3000)

    async def reconcile_until(status):
        # A publication can legitimately lose its short evidence deadline while
        # waiting for PostgreSQL. The next pass must obtain a fresh observation.
        with fail_after(10):
            while True:
                await reconciler.run_once()
                async with short_session(environment_service.sessions) as session:
                    row = await session.get(EnvironmentRecord, target.environment_id)
                    assert row.generation == target.generation
                    if row.status == status:
                        return
                await asyncio.sleep(0.05)

    async with create_task_group() as tasks:
        tasks.start_soon(keep_online)
        try:
            await reconcile_until("running")
        finally:
            tasks.cancel_scope.cancel()
    await client_service.coordination.retire(connection)
    await reconcile_until("unavailable")


async def test_reconciliation_defers_dependency_failure_without_pg_mutation(
    client_service, environment_service, target, redis_client
):
    assert await client_service.resources.publish(
        target, "running", publication_id=new_object_id("aud"), evidence_deadline=LeaseDeadline(monotonic() + 10)
    )
    await redis_client.lpush(environment_key(target.organization_id, target.environment_id), "invalid-state")
    await ClientConnectionReconciler(client_service).run_once()
    async with short_session(environment_service.sessions) as session:
        row = await session.get(EnvironmentRecord, target.environment_id)
        assert row.status == "running"
        assert row.operation_generation == target.operation_generation + 1


@pytest.mark.parametrize(
    "origin",
    [
        "ws://example.com",
        "https://example.com",
        "wss://user:secret@example.com",
        "wss://example.com/path",
        "wss://example.com?",
        "wss://example.com#",
        "wss://example.com:0",
        "wss://example.com:99999",
        " wss://example.com",
        "wss://example.com\n",
        "wss:///",
        "wss://example.com\\evil",
    ],
)
def test_public_origin_rejects_credentials_paths_and_non_wss(origin):
    with pytest.raises(ValueError):
        websocket_origin(origin)
