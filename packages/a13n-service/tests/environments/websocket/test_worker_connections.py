"""Shared Worker use ownership, cancellation and isolation on real Redis."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import EnvironmentAccess
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.iam.attempts import AttemptAuthorization
from a13n_service.interactions.attempts import AttemptContext, AttemptLease
from a13n_service.temporal import utc_now

pytestmark = pytest.mark.anyio
FULL = frozenset(EnvironmentAction)
READ_ONLY = EnvironmentAccess("read_only").permission_set().operations


@pytest.fixture
async def worker(relay_redis):
    owner = WorkerClientConnections(relay_redis, relay_redis, "worker", max_uses=2, max_mounts=4)
    await owner.prepare()
    try:
        yield owner
    finally:
        await owner.close()


@pytest.fixture
def attempt():
    # IAM persistence is covered by the real-PostgreSQL mount authorization tests.
    return AttemptContext(
        organization_id="org",
        thread_id="thread",
        run_id="run",
        run_attempt_id="attempt",
        attempt_number=1,
        lease_token="token",
        worker_id="worker",
        worker_build_id="test",
        lease_duration=timedelta(seconds=30),
        renewal_interval=timedelta(seconds=10),
        renewal_timeout=timedelta(seconds=1),
        reconciliation_timeout=timedelta(seconds=1),
        cleanup_timeout=timedelta(seconds=1),
        lease=AttemptLease(utc_now() + timedelta(seconds=30)),
        authorization=Mock(spec=AttemptAuthorization),
    )


async def connect(worker, environment_id="env"):
    coordination = worker._coordination
    ticket = await coordination.issue("org", environment_id)
    admitted = await coordination.admit("org", environment_id, ticket=ticket.secret, owner_instance_id="control")
    connection = admitted.value.connection
    await asyncio.sleep((admitted.value.barrier_ms - admitted.value.now_ms) / 1000 + 0.02)
    await coordination.promote(connection)
    await coordination.online(connection)
    return connection


async def test_aliases_share_one_grant_and_only_last_release_retires_carrier(worker, attempt, monkeypatch):
    await connect(worker)
    acquire = AsyncMock(wraps=worker._coordination.acquire_use)
    monkeypatch.setattr(worker._coordination, "acquire_use", acquire)
    reader, writer = await asyncio.gather(
        worker.acquire(attempt, "env", READ_ONLY, mount_name="reader"),
        worker.acquire(attempt, "env", FULL, mount_name="writer"),
    )
    assert reader is not writer and reader.identity == writer.identity
    assert reader._mount_id != writer._mount_id
    assert acquire.await_count == 1
    with pytest.raises(EnvironmentError) as denied:
        await reader.call("file.write_text", {"path": "/no", "text": "no", "mode": "create"})
    assert denied.value.code == "environment_forbidden"
    await worker.release(reader)
    await worker.release(reader)
    assert not reader.available and writer.available
    assert (await worker._coordination.observe("org", "env")).value.use.identity == writer.identity
    await worker.release(writer)
    assert (await worker._coordination.observe("org", "env")).value.status == "offline"
    assert not worker._slots


async def test_other_attempt_and_duplicate_mount_cannot_disturb_an_owned_use(worker, attempt):
    await connect(worker)
    client = await worker.acquire(attempt, "env", FULL)
    for contender, name in (
        (replace(attempt, run_attempt_id="other", run_id="other-run"), "computer"),
        (attempt, "workspace"),
    ):
        with pytest.raises(EnvironmentError) as rejected:
            await worker.acquire(contender, "env", FULL, mount_name=name)
        assert rejected.value.code == "environment_busy"
    assert client.available
    assert len(worker._slots) == 1


async def test_distinct_environments_acquire_in_parallel(worker, attempt, monkeypatch):
    await asyncio.gather(connect(worker, "first"), connect(worker, "second"))
    acquire = worker._coordination.acquire_use
    entered = set()
    both = asyncio.Event()

    async def concurrent(identity, **kwargs):
        entered.add(identity.connection.environment_id)
        if len(entered) == 2:
            both.set()
        await both.wait()
        return await acquire(identity, **kwargs)

    monkeypatch.setattr(worker._coordination, "acquire_use", concurrent)
    async with asyncio.timeout(1):
        first, second = await asyncio.gather(
            worker.acquire(attempt, "first", FULL), worker.acquire(attempt, "second", FULL)
        )
    assert first.identity != second.identity and first.available and second.available


@pytest.mark.parametrize("exit_reason", ["cancel", "drain"])
async def test_abandoned_acquisition_releases_its_grant_and_slot(worker, attempt, monkeypatch, exit_reason):
    await connect(worker)
    acquire = worker._coordination.acquire_use
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed(identity, **kwargs):
        observed = await acquire(identity, **kwargs)
        entered.set()
        await resume.wait()
        return observed

    monkeypatch.setattr(worker._coordination, "acquire_use", delayed)
    opening = asyncio.create_task(worker.acquire(attempt, "env", FULL))
    await entered.wait()
    if exit_reason == "cancel":
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
    else:
        await worker.close()
        resume.set()
        with pytest.raises(EnvironmentError) as rejected:
            await opening
        assert rejected.value.code == "environment_unavailable"
    assert not worker._slots
    assert (await worker._coordination.observe("org", "env")).value.status == "offline"


async def test_cancelled_waiter_does_not_cancel_another_mount_acquisition(worker, attempt, monkeypatch):
    await connect(worker)
    acquire = worker._coordination.acquire_use
    entered, resume = asyncio.Event(), asyncio.Event()

    async def delayed(identity, **kwargs):
        observed = await acquire(identity, **kwargs)
        entered.set()
        await resume.wait()
        return observed

    monkeypatch.setattr(worker._coordination, "acquire_use", delayed)
    first = asyncio.create_task(worker.acquire(attempt, "env", FULL, mount_name="first"))
    await entered.wait()
    second = asyncio.create_task(worker.acquire(attempt, "env", FULL, mount_name="second"))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    resume.set()
    client = await first
    assert client.available and len(worker._slots) == 1
    await worker.release(client)
    assert not worker._slots


async def test_attempt_loss_fences_every_alias_and_releases_shared_use(worker, attempt):
    await connect(worker)
    reader = await worker.acquire(attempt, "env", READ_ONLY, mount_name="reader")
    writer = await worker.acquire(attempt, "env", FULL, mount_name="writer")
    use = next(iter(worker._slots.values())).use
    attempt.lease.invalidate()
    assert not reader.available and not writer.available
    await worker._renew(use)
    assert not worker._slots
    assert (await worker._coordination.observe("org", "env")).value.status == "offline"


async def test_one_renewal_advances_authority_for_every_alias(worker, attempt):
    connection = await connect(worker)
    reader = await worker.acquire(attempt, "env", READ_ONLY, mount_name="reader")
    writer = await worker.acquire(replace(attempt), "env", FULL, mount_name="writer")
    original = reader._scope.authority.deadline
    await asyncio.sleep(0.02)
    await worker._coordination.renew(connection)
    await worker._renew(next(iter(worker._slots.values())).use)
    assert reader._scope.authority.deadline > original
    assert writer._scope.authority.deadline == reader._scope.authority.deadline
    assert reader.available and writer.available


async def test_mount_capacity_is_reclaimed_without_retiring_shared_connection(worker, attempt):
    await connect(worker)
    clients = await asyncio.gather(
        *(worker.acquire(attempt, "env", FULL, mount_name=f"mount-{index}") for index in range(4))
    )
    with pytest.raises(EnvironmentError) as rejected:
        await worker.acquire(attempt, "env", FULL, mount_name="overflow")
    assert rejected.value.code == "environment_overloaded"
    await worker.release(clients[0])
    replacement = await worker.acquire(attempt, "env", READ_ONLY, mount_name="replacement")
    assert replacement.identity == clients[1].identity
    assert all(client.available for client in clients[1:])
