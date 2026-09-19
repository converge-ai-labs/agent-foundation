from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mounted_attempt(interaction_sessions, interaction_object_store, client_environment):
    _, _, environment = client_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as database:
        database.add(accepted_mount(run.id, environment.id))
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    return _authority(claim), environment


async def test_boundary_snapshot_does_not_absorb_later_additions(interaction_sessions, mounted_attempt):
    attempt, environment = mounted_attempt
    store = RunMountObservations(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    captured = await store.snapshot(attempt)
    async with transaction(interaction_sessions) as database:
        database.add(
            accepted_mount(attempt.run_id, environment.id, name="second", created_at=NOW + timedelta(microseconds=1))
        )
    assert [mount.name for mount in captured] == ["computer"]
    assert [mount.name for mount in await store.snapshot(attempt)] == ["computer", "second"]
    assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0


async def test_observations_clear_failure_and_preserve_acceptance_and_first_use(interaction_sessions, mounted_attempt):
    attempt, _ = mounted_attempt
    now = NOW + timedelta(seconds=3)
    store = RunMountObservations(interaction_sessions, clock=lambda: now)
    mount = (await store.snapshot(attempt))[0]
    async with transaction(interaction_sessions) as database:
        row = await database.get(RunEnvironmentMountRecord, (mount.run_id, mount.name))
        row.use_started_at = NOW
    failure = SafeFailure(code="environment_unavailable", message="The client is offline")
    await store.publish(attempt, mount, "failed", error=failure)
    async with short_session(interaction_sessions) as database:
        row = await database.get(RunEnvironmentMountRecord, (mount.run_id, mount.name))
        assert row.error == failure.model_dump(mode="json")
    now -= timedelta(seconds=1)
    await store.publish(attempt, mount, "preparing")
    await store.validate(attempt, mount)
    await store.publish(attempt, mount, "ready")
    await store.publish(attempt, mount, "ready")
    assert await store.snapshot(attempt) == (mount,)
    async with short_session(interaction_sessions) as database:
        row = await database.get(RunEnvironmentMountRecord, (mount.run_id, mount.name))
        assert row.application_status == "ready" and row.error is None
        assert row.applied_attempt_id == attempt.run_attempt_id and row.applied_attempt_fence == attempt.attempt_number
        assert assume_utc(row.observed_at) > NOW + timedelta(seconds=3)
        assert assume_utc(row.use_started_at) == NOW
        assert assume_utc(row.created_at) == NOW


async def test_takeover_rebuilds_from_acceptance_and_rejects_every_old_worker_write(
    interaction_sessions, mounted_attempt
):
    old, _ = mounted_attempt
    now = NOW + timedelta(seconds=2)
    store = RunMountObservations(interaction_sessions, clock=lambda: now)
    mount = (await store.snapshot(old))[0]
    await store.publish(old, mount, "ready")
    now = NOW + timedelta(seconds=32)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: now, lifecycle=test_lifecycle_writer()).claim(
        old.run_id, _worker(worker_id="worker-2")
    )
    assert isinstance(claim, ClaimedAttempt)
    successor = _authority(claim)
    assert await store.snapshot(successor) == (mount,)
    await store.publish(successor, mount, "preparing")
    # An optimistic local deadline must not bypass PostgreSQL's successor fence.
    old.lease.confirm_renewal(now + timedelta(seconds=10))
    with pytest.raises(AttemptAuthorityError):
        await store.snapshot(old)
    with pytest.raises(AttemptAuthorityError):
        await store.validate(old, mount)
    for status in ("preparing", "ready", "failed"):
        with pytest.raises(AttemptAuthorityError):
            await store.publish(old, mount, status)
    async with short_session(interaction_sessions) as database:
        row = await database.get(RunEnvironmentMountRecord, (mount.run_id, mount.name))
        assert row.applied_attempt_id == successor.run_attempt_id
        assert row.application_status == "preparing"


async def test_locally_invalidated_attempt_cannot_publish_even_before_database_expiry(
    interaction_sessions, mounted_attempt
):
    attempt, _ = mounted_attempt
    store = RunMountObservations(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    mount = (await store.snapshot(attempt))[0]
    attempt.lease.invalidate()
    with pytest.raises(AttemptAuthorityError):
        await store.snapshot(attempt)
    with pytest.raises(AttemptAuthorityError):
        await store.validate(attempt, mount)
    with pytest.raises(AttemptAuthorityError):
        await store.publish(attempt, mount, "ready")


@pytest.mark.parametrize("changes", [{"run_id": "different"}, {"name": "missing"}])
async def test_worker_cannot_publish_a_fabricated_association(interaction_sessions, mounted_attempt, changes):
    attempt, _ = mounted_attempt
    store = RunMountObservations(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    mount = (await store.snapshot(attempt))[0]
    with pytest.raises(ValueError):
        await store.validate(attempt, replace(mount, **changes))
    with pytest.raises(ValueError):
        await store.publish(attempt, replace(mount, **changes), "ready")
    async with short_session(interaction_sessions) as database:
        row = await database.get(RunEnvironmentMountRecord, (mount.run_id, mount.name))
        assert row.application_status == "pending" and row.applied_attempt_id is None
