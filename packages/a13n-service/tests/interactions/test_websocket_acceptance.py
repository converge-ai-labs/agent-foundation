from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

import pytest
from a13n_service.application_errors import ErrorCategory
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.websocket.admission import OnlineAdmission
from a13n_service.environments.websocket.coordination import ConnectionCoordination, CoordinationError
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select, text

from tests.environments.websocket.conftest import relay_redis as relay_redis

from .conftest import ORGANIZATION_ID
from .test_attempt_execution import _accept_root
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


async def _connect(coordination, environment_id, *, online=True):
    ticket = await coordination.issue(ORGANIZATION_ID, environment_id)
    candidate = await coordination.admit(
        ORGANIZATION_ID, environment_id, ticket=ticket.secret, owner_instance_id="control"
    )
    connection = candidate.value.connection
    assert connection is not None
    await asyncio.sleep(coordination.limits.lease_ms / 1000 + 0.02)
    await coordination.promote(connection)
    if online:
        await coordination.online(connection)
    return connection


async def _assert_unaccepted(sessions):
    async with short_session(sessions) as database:
        for model in (RunRecord, ThreadRecord, SessionRecord):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("presence", ["offline", "connecting", "takeover"])
async def test_unavailable_primary_rolls_back_acceptance(
    interaction_sessions, interaction_object_store, client_environment, relay_redis, presence
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    if presence != "offline":
        await _connect(coordination, environment.id, online=presence == "takeover")
    if presence == "takeover":
        ticket = await coordination.issue(ORGANIZATION_ID, environment.id)
        await coordination.admit(ORGANIZATION_ID, environment.id, ticket=ticket.secret, owner_instance_id="new-control")
    with pytest.raises(EnvironmentManagementError) as caught:
        await _accept_root(
            interaction_sessions,
            interaction_object_store,
            environment_id=environment.id,
            environment_working_directory="/projects/primary",
            coordination=coordination,
        )
    assert caught.value.code == "environment_unavailable"
    assert caught.value.category == ErrorCategory.conflict
    await _assert_unaccepted(interaction_sessions)


async def test_online_acceptance_observes_outside_database_and_replays_after_disconnect(
    interaction_sessions, interaction_object_store, client_environment, relay_redis, monkeypatch
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    connection = await _connect(coordination, environment.id)
    original = coordination.observe
    calls = 0

    async def observe(*target):
        nonlocal calls
        calls += 1
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        return await original(*target)

    monkeypatch.setattr(coordination, "observe", observe)
    _, accepted, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory="/projects/primary",
        coordination=coordination,
        idempotency_key="online-acceptance",
    )
    assert calls == 1
    await coordination.retire(connection)
    _, replay, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory="/projects/primary",
        coordination=coordination,
        idempotency_key="online-acceptance",
    )
    assert replay == accepted
    assert calls == 1
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
        row = await database.get(RunRecord, accepted.id)
        assert row.environment_id == environment.id


@pytest.mark.parametrize("failure", ["missing", "redis", "expired"])
async def test_dependency_uncertainty_never_accepts(
    interaction_sessions, interaction_object_store, client_environment, relay_redis, monkeypatch, failure
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    await _connect(coordination, environment.id)
    original = coordination.observe
    calls = 0

    async def observe(*target):
        nonlocal calls
        calls += 1
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        if failure == "redis":
            raise CoordinationError("coordination_unavailable")
        observation = await original(*target)
        return replace(observation, request_started_at=monotonic() - 60)

    monkeypatch.setattr(coordination, "observe", observe)
    with pytest.raises(EnvironmentManagementError) as caught:
        await _accept_root(
            interaction_sessions,
            interaction_object_store,
            environment_id=environment.id,
            environment_working_directory="/projects/primary",
            coordination=None if failure == "missing" else coordination,
        )
    assert caught.value.category == ErrorCategory.unavailable
    assert calls <= 4
    await _assert_unaccepted(interaction_sessions)


async def test_changed_provider_is_revalidated_after_presence_read(
    interaction_sessions, interaction_object_store, client_environment, relay_redis, monkeypatch
):
    _, provider, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    await _connect(coordination, environment.id)
    original = coordination.observe

    async def observe(*target):
        result = await original(*target)
        async with transaction(interaction_sessions) as database:
            row = await database.get(EnvironmentProviderRecord, provider.id)
            row.enabled = False
        return result

    monkeypatch.setattr(coordination, "observe", observe)
    with pytest.raises(EnvironmentManagementError) as caught:
        await _accept_root(
            interaction_sessions,
            interaction_object_store,
            environment_id=environment.id,
            environment_working_directory="/projects/primary",
            coordination=coordination,
        )
    assert caught.value.code == "environment_invalid"
    await _assert_unaccepted(interaction_sessions)


async def test_concurrent_acceptance_replays_even_when_presence_then_goes_offline(
    interaction_sessions, interaction_object_store, client_environment, relay_redis, monkeypatch
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    peer = ConnectionCoordination(relay_redis)
    connection = await _connect(coordination, environment.id)
    original = coordination.observe

    async def observe(*target):
        await _accept_root(
            interaction_sessions,
            interaction_object_store,
            environment_id=environment.id,
            environment_working_directory="/projects/primary",
            coordination=peer,
            idempotency_key="concurrent-online-acceptance",
        )
        await peer.retire(connection)
        return await original(*target)

    monkeypatch.setattr(coordination, "observe", observe)
    await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id,
        environment_working_directory="/projects/primary",
        coordination=coordination,
        idempotency_key="concurrent-online-acceptance",
    )
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1


async def test_expired_commit_evidence_rolls_back_all_relational_effects_before_refresh(
    interaction_sessions, client_environment, relay_redis, monkeypatch
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    await _connect(coordination, environment.id)
    original = coordination.observe
    observations = 0
    attempts = 0

    async def observe(*target):
        nonlocal observations
        observations += 1
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        result = await original(*target)
        # The first response has spent most of its grant horizon in transit.
        return replace(result, request_started_at=result.request_started_at - 1.8) if observations == 1 else result

    async def accept(database, online):
        nonlocal attempts
        attempts += 1
        row = await database.get(EnvironmentRecord, environment.id, with_for_update=True)
        assert row.labels == {}
        online.require(ORGANIZATION_ID, environment.id)
        row.labels = {"acceptance": str(attempts)}
        if attempts == 2:
            # Slow SQL may consume the remaining horizon even without external I/O.
            await database.execute(text("SELECT pg_sleep(0.25)"))
        return attempts

    monkeypatch.setattr(coordination, "observe", observe)
    assert await OnlineAdmission(interaction_sessions, coordination).commit(accept) == 3
    assert observations == 2
    async with short_session(interaction_sessions) as database:
        assert (await database.get(EnvironmentRecord, environment.id)).labels == {"acceptance": "3"}


async def test_online_admission_deadline_cancels_slow_sql_and_releases_the_transaction(
    interaction_sessions, client_environment, relay_redis
):
    _, _, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    await _connect(coordination, environment.id)

    async def accept(database, online):
        row = await database.get(EnvironmentRecord, environment.id, with_for_update=True)
        online.require(ORGANIZATION_ID, environment.id)
        row.labels = {"uncommitted": "yes"}
        await database.flush()
        await database.execute(text("SELECT pg_sleep(30)"))

    async with asyncio.timeout(10):
        with pytest.raises(EnvironmentManagementError) as caught:
            await OnlineAdmission(interaction_sessions, coordination).commit(accept)
    assert caught.value.category == ErrorCategory.unavailable
    assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
    async with short_session(interaction_sessions) as database:
        assert (await database.get(EnvironmentRecord, environment.id)).labels == {}
