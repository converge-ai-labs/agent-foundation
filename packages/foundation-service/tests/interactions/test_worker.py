from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.interactions.objects import StaleStateWriter
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, RunCandidate, WorkerClaim
from a13n_service.interactions.worker import WorkerExecutionLoop, WorkerIdentity
from a13n_service.storage import short_session
from anyio import Event, create_task_group, fail_after, sleep
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _accept_root

pytestmark = pytest.mark.anyio


@dataclass
class _Attempt:
    started: Event = field(default_factory=Event)
    stopped: Event = field(default_factory=Event)
    finish: Event = field(default_factory=Event)
    handoffs: list[RunAttemptYieldReason] = field(default_factory=list)
    starts: int = 0
    failure: Exception | None = None

    async def run(self) -> None:
        self.starts += 1
        self.started.set()
        try:
            await self.finish.wait()
            if self.failure is not None:
                raise self.failure
        finally:
            self.stopped.set()

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None:
        self.handoffs.append(reason)


@dataclass
class _Preflight:
    attempt: _Attempt = field(default_factory=_Attempt)
    ready: Event = field(default_factory=Event)
    proceed: Event = field(default_factory=Event)
    blocked: bool = False
    declined: bool = False
    candidates: list[RunCandidate] = field(default_factory=list)
    claims: list[ClaimedAttempt] = field(default_factory=list)

    async def prepare(self, candidate):
        self.candidates.append(candidate)
        self.ready.set()
        if self.blocked:
            await self.proceed.wait()
        return None if self.declined else self

    def create(self, claimed, capacity_slot):
        self.claims.append(claimed)
        return self.attempt


class _Scheduler(AttemptScheduler):
    loop: WorkerExecutionLoop
    claims = 0
    scans = 0

    async def discover(self, **kwargs):
        self.scans += 1
        return await super().discover(**kwargs)

    async def claim(self, run_id: str, claim: WorkerClaim):
        assert self.loop.active_count == 1, "claim requires a reserved execution slot"
        self.claims += 1
        return await super().claim(run_id, claim)


def _loop(sessions, preflight, *, scan_limit=32):
    scheduler = _Scheduler(sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer())
    loop = WorkerExecutionLoop(
        scheduler,
        preflight,
        identity=WorkerIdentity("worker-1", "generation-1", "build-1"),
        lease_duration=timedelta(seconds=30),
        handoff_preference_window=timedelta(seconds=30),
        concurrency=1,
        scan_limit=scan_limit,
        poll_interval_seconds=0.01,
    )
    scheduler.loop = loop
    return scheduler, loop


async def test_loop_reserves_before_claim_and_keeps_capacity_until_attempt_stops(
    interaction_sessions, interaction_object_store
):
    await _accept_root(interaction_sessions, interaction_object_store)
    preflight = _Preflight()
    scheduler, loop = _loop(interaction_sessions, preflight)
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.attempt.started.wait()
            assert loop.active_count == 1
            await sleep(0.05)
            assert (scheduler.scans, scheduler.claims, preflight.attempt.starts) == (1, 1, 1)
            await loop.drain()
            assert preflight.attempt.handoffs == [RunAttemptYieldReason.service_drain]
            assert loop.active_count == 1
            assert not preflight.attempt.stopped.is_set()
            preflight.attempt.finish.set()
            await loop.wait_stopped()
    assert loop.active_count == 0
    assert preflight.claims[0].attempt.organization_id == ORGANIZATION_ID


async def test_drain_during_preflight_never_claims(interaction_sessions, interaction_object_store):
    await _accept_root(interaction_sessions, interaction_object_store)
    preflight = _Preflight(blocked=True)
    scheduler, loop = _loop(interaction_sessions, preflight)
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.ready.wait()
            await loop.drain()
            preflight.proceed.set()
            await loop.wait_stopped()
    assert scheduler.claims == 0
    assert loop.active_count == 0
    async with short_session(interaction_sessions) as session:
        assert (await session.scalars(select(RunAttemptRecord))).all() == []


async def test_incompatible_runtime_is_not_claimed(interaction_sessions, interaction_object_store):
    await _accept_root(interaction_sessions, interaction_object_store)
    preflight = _Preflight(declined=True)
    scheduler, loop = _loop(interaction_sessions, preflight, scan_limit=1)
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.ready.wait()
            await loop.drain()
            await loop.wait_stopped()
    assert scheduler.claims == 0
    assert preflight.claims == []
    assert loop.active_count == 0


async def test_process_cancellation_joins_attempt_before_releasing_capacity(
    interaction_sessions, interaction_object_store
):
    await _accept_root(interaction_sessions, interaction_object_store)
    preflight = _Preflight()
    _, loop = _loop(interaction_sessions, preflight)
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.attempt.started.wait()
            tasks.cancel_scope.cancel()
    assert preflight.attempt.stopped.is_set()
    assert loop.active_count == 0
    await loop.wait_stopped()


async def test_discovery_is_bounded_and_cursor_does_not_repeat_a_candidate(
    interaction_sessions, interaction_object_store
):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )
    args = dict(worker_build_id="build-1", handoff_preference_window=timedelta(seconds=30), limit=1)
    page = await scheduler.discover(**args)
    assert tuple(candidate.run_id for candidate in page) == (run.id,)
    assert await scheduler.discover(**args, after=page[0].position) == ()
    assert await scheduler.discover(**args, runtime_lock_digest="b" * 64) == ()
    assert await scheduler.discover(**args, queue_names=("another",)) == ()


@pytest.mark.parametrize(
    "failure",
    [
        AttemptAuthorityError("lease lost"),
        ExceptionGroup("executor", [AttemptAuthorityError("lease lost")]),
        ExceptionGroup("executor", [StaleStateWriter("writer superseded")]),
    ],
)
async def test_attempt_authority_loss_does_not_stop_worker(interaction_sessions, interaction_object_store, failure):
    await _accept_root(interaction_sessions, interaction_object_store)
    preflight = _Preflight(attempt=_Attempt(failure=failure))
    _, loop = _loop(interaction_sessions, preflight)
    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.attempt.started.wait()
            preflight.attempt.finish.set()
            await preflight.attempt.stopped.wait()
            while loop.active_count:
                await sleep(0)
            assert not loop.is_draining()
            await loop.drain()
            await loop.wait_stopped()


async def test_mixed_executor_failure_still_stops_worker(interaction_sessions, interaction_object_store):
    await _accept_root(interaction_sessions, interaction_object_store)
    failure = ExceptionGroup("executor", [AttemptAuthorityError("lease lost"), ValueError("broken composition")])
    preflight = _Preflight(attempt=_Attempt(failure=failure))
    _, loop = _loop(interaction_sessions, preflight)
    with fail_after(5), pytest.raises(ExceptionGroup) as raised:
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await preflight.attempt.started.wait()
            preflight.attempt.finish.set()
            await loop.wait_stopped()
    assert raised.value.subgroup(ValueError) is not None
    assert raised.value.subgroup(AttemptAuthorityError) is None
    assert loop.active_count == 0
