"""Renewal locks only its Attempt and serializes with terminal transitions."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after, sleep
from sqlalchemy import select, text

from tests.lifecycle_support import test_lifecycle_writer
from tests.sql_capture import capture_sql

from .conftest import NOW
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def heartbeat_case(interaction_sessions, interaction_object_store):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    return interaction_sessions, interaction_object_store, claim, _authority(claim)


async def wait_for_blocked_heartbeat(sessions):
    with fail_after(5):
        while True:
            async with short_session(sessions) as database:
                blocked = await database.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query LIKE '%FOR UPDATE OF run_attempts%')"
                    )
                )
            if blocked:
                return
            await sleep(0.01)


async def test_heartbeat_renews_while_thread_and_run_are_locked(heartbeat_case):
    sessions, _, claim, authority = heartbeat_case
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    async with transaction(sessions) as blocker:
        thread = await blocker.scalar(
            select(ThreadRecord).where(ThreadRecord.id == authority.thread_id).with_for_update()
        )
        run = await blocker.scalar(select(RunRecord).where(RunRecord.id == authority.run_id).with_for_update())
        with fail_after(5), capture_sql(sessions) as statements:
            renewed = await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
        assert len(statements) == 2, statements
        assert "FOR UPDATE OF run_attempts" in statements[0]
        assert "input_json" not in statements[0] and "output_json" not in statements[0]
        assert renewed.run_version == run.version == claim.run_version
        assert thread.version == 1
    assert renewed.attempt_version == claim.attempt.version + 1
    assert renewed.lease_expires_at == NOW + timedelta(seconds=32)


@pytest.mark.parametrize("offset", [0, 1])
async def test_heartbeat_checks_expiry_after_waiting_for_attempt_lock(heartbeat_case, offset):
    sessions, _, claim, authority = heartbeat_case
    now = NOW + timedelta(seconds=2)
    execution = AttemptExecutionService(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())

    async def renew():
        with pytest.raises(AttemptAuthorityError):
            await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))

    async with create_task_group() as tasks:
        async with transaction(sessions) as blocker:
            await blocker.scalar(
                select(RunAttemptRecord).where(RunAttemptRecord.id == authority.run_attempt_id).with_for_update()
            )
            tasks.start_soon(renew)
            await wait_for_blocked_heartbeat(sessions)
            now = claim.attempt.lease_expires_at + timedelta(seconds=offset)
    async with short_session(sessions) as database:
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        assert attempt.version == claim.attempt.version
        assert attempt.lease_expires_at == claim.attempt.lease_expires_at


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", "other-org"),
        ("thread_id", "other-thread"),
        ("run_id", "other-run"),
        ("run_attempt_id", "other-attempt"),
        ("attempt_number", 99),
        ("worker_id", "other-worker"),
        ("worker_build_id", "other-build"),
        ("lease_token", "wrong-token"),
    ],
)
async def test_heartbeat_preserves_all_authority_checks(heartbeat_case, field, value):
    sessions, _, claim, authority = heartbeat_case
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    with pytest.raises(AttemptAuthorityError):
        await execution.heartbeat(replace(authority, **{field: value}), lease_duration=timedelta(seconds=30))
    async with short_session(sessions) as database:
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        assert attempt.version == claim.attempt.version
        assert attempt.lease_expires_at == claim.attempt.lease_expires_at


@pytest.mark.parametrize("transition", ["cancel", "yield", "takeover"])
async def test_waiting_heartbeat_cannot_revive_terminal_attempt(heartbeat_case, monkeypatch, transition):
    sessions, objects, claim, authority = heartbeat_case
    lifecycle = test_lifecycle_writer()
    terminalizing, release = Event(), Event()
    now = NOW + timedelta(seconds=2)
    if transition == "takeover":
        now = claim.attempt.lease_expires_at + timedelta(seconds=1)

    # Pause the real transition after its lifecycle writes, with its transaction
    # still holding the old Attempt lock. A concurrent heartbeat must wait.
    method = "append_run_with_attempt_lifecycle" if transition == "cancel" else "append_run_attempt_lifecycle"
    append = getattr(lifecycle, method)

    async def pause(*args, **kwargs):
        await append(*args, **kwargs)
        terminalizing.set()
        await release.wait()

    monkeypatch.setattr(lifecycle, method, pause)
    execution = AttemptExecutionService(sessions, clock=lambda: now, lifecycle=lifecycle)

    async def terminate():
        if transition == "cancel":
            await RunOutcomeService(sessions, RunPayloadStore(objects), clock=lambda: now, lifecycle=lifecycle).cancel(
                organization_id=authority.organization_id,
                run_id=authority.run_id,
                expected_run_version=claim.run_version,
                expected_thread_version=1,
                failure=SafeFailure(code="test_cancel", message="Cancelled by the test"),
            )
        elif transition == "yield":
            await execution.yield_attempt(authority, RunAttemptYieldReason.service_drain)
        else:
            replacement = await AttemptScheduler(sessions, clock=lambda: now, lifecycle=lifecycle).claim(
                authority.run_id, _worker(worker_id="replacement-worker")
            )
            assert isinstance(replacement, ClaimedAttempt)
            assert replacement.attempt.attempt_number == claim.attempt.attempt_number + 1

    async def renew():
        with pytest.raises(AttemptAuthorityError):
            await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))

    with fail_after(10):
        async with create_task_group() as tasks:
            tasks.start_soon(terminate)
            await terminalizing.wait()
            tasks.start_soon(renew)
            await wait_for_blocked_heartbeat(sessions)
            release.set()
    async with short_session(sessions) as database:
        attempts = tuple(
            await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == authority.run_id))
        )
        assert len(attempts) == (2 if transition == "takeover" else 1)
        old = next(attempt for attempt in attempts if attempt.id == authority.run_attempt_id)
        assert old.status == {"cancel": "cancelled", "yield": "yielded", "takeover": "failed"}[transition]
        assert old.lease_expires_at == now


async def test_renewal_prevents_takeover_using_stale_scan_candidate(heartbeat_case):
    sessions, _, claim, authority = heartbeat_case
    scheduler = AttemptScheduler(
        sessions, clock=lambda: claim.attempt.lease_expires_at, lifecycle=test_lifecycle_writer()
    )
    assert await scheduler.scan(_worker(), queue_name="default") == (authority.run_id,)
    # A scan is only a hint: a renewal committed before the claim is evaluated
    # must be observed when the claimant locks the selected Attempt.
    execution = AttemptExecutionService(
        sessions, clock=lambda: claim.attempt.lease_expires_at - timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )
    await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
    assert await scheduler.claim(authority.run_id, _worker(worker_id="other-worker")) is None
    async with short_session(sessions) as database:
        attempts = tuple(
            await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == authority.run_id))
        )
        assert len(attempts) == 1 and attempts[0].id == claim.attempt.id
