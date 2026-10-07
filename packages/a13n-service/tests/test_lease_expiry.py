"""Lease recovery isolates contention and failures while retaining fenced, atomic transitions."""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_service.infra.db import lock, transaction
from a13n_service.runs import seal as seal_module
from a13n_service.runs.attempts import Lease
from a13n_service.runs.claim import claim
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


async def _expired(service: SimpleNamespace, scripted_model, runs_kit: SimpleNamespace, count: int) -> list[Lease]:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    for index in range(count):
        await runs_kit.start_thread(service, agent, str(index))
    leases = await claim(service.runtime, worker_id="dead-worker", worker_build="test", limit=count)
    # Equal timestamps exercise the ID tie-breaker at every page boundary.
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow)
            .where(AttemptRow.id.in_([lease.attempt_id for lease in leases]))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    return sorted(leases, key=lambda lease: lease.attempt_id)


async def _statuses(service: SimpleNamespace) -> dict[str, str]:
    async with transaction(service.runtime.storage) as session:
        return dict((await session.execute(select(RunRow.id, RunRow.status))).tuples().all())


@pytest.mark.parametrize("row_type", [ThreadRow, RunRow, AttemptRow])
async def test_locked_pages_do_not_starve_later_runs_and_release_partial_locks(
    service, scripted_model, runs_kit, row_type
) -> None:  # type: ignore[no-untyped-def]
    leases = await _expired(service, scripted_model, runs_kit, 3)
    sweep = LeaseExpirer(service.runtime, batch=2)
    async with transaction(service.runtime.storage) as blocker:
        for lease in leases[:2]:
            row_id = {ThreadRow: lease.thread_id, RunRow: lease.run_id, AttemptRow: lease.attempt_id}[row_type]
            await lock(blocker, row_type, row_id)
        async with asyncio.timeout(2):
            await sweep()
            assert set((await _statuses(service)).values()) == {"running"}
            await sweep()
        states = await _statuses(service)
        assert [states[lease.run_id] for lease in leases] == ["running", "running", "accepted"]
        if row_type is not ThreadRow:
            async with transaction(service.runtime.storage) as observer:
                # Skipping a later lock must release the earlier thread lock immediately.
                assert await lock(observer, ThreadRow, leases[0].thread_id, skip_locked=True) is not None
    await sweep()
    assert set((await _statuses(service)).values()) == {"accepted"}


async def test_failed_pages_do_not_starve_later_runs_and_are_revisited(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    leases = await _expired(service, scripted_model, runs_kit, 3)
    failing = {lease.run_id for lease in leases[:2]}
    recover = seal_module.recover

    async def fail_first_page(session, runtime, thread, run, attempt, **fields) -> None:  # type: ignore[no-untyped-def]
        if run.id in failing:
            raise RuntimeError("Cannot recover this candidate")
        await recover(session, runtime, thread, run, attempt, **fields)

    monkeypatch.setattr(seal_module, "recover", fail_first_page)
    sweep = LeaseExpirer(service.runtime, batch=2)
    await sweep()
    await sweep()
    states = await _statuses(service)
    assert [states[lease.run_id] for lease in leases] == ["running", "running", "accepted"]
    failing.clear()
    await sweep()
    assert set((await _statuses(service)).values()) == {"accepted"}


async def test_concurrent_expiry_sweeps_recover_each_attempt_once(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    leases = await _expired(service, scripted_model, runs_kit, 4)
    recovered: Counter[str] = Counter()
    recover = seal_module.recover

    async def record(session, runtime, thread, run, attempt, **fields) -> None:  # type: ignore[no-untyped-def]
        await recover(session, runtime, thread, run, attempt, **fields)
        recovered[attempt.id] += 1

    monkeypatch.setattr(seal_module, "recover", record)
    sweeps = [LeaseExpirer(service.runtime, batch=4) for _ in range(2)]
    await asyncio.gather(*(sweep() for sweep in sweeps))
    assert recovered == Counter({lease.attempt_id: 1 for lease in leases})
    assert set((await _statuses(service)).values()) == {"accepted"}
    async with transaction(service.runtime.storage) as session:
        attempts = (await session.scalars(select(AttemptRow))).all()
        assert len(attempts) == len(leases)
        assert all(attempt.status == "failed" and attempt.failure["code"] == "lease_expired" for attempt in attempts)


async def test_a_stale_candidate_cannot_expire_a_replacement_or_an_unexpired_lease(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    (previous,) = await _expired(service, scripted_model, runs_kit, 1)
    await LeaseExpirer(service.runtime, batch=1)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == previous.run_id).values(available_at=RunRow.created_at))
    (current,) = await claim(service.runtime, worker_id="replacement", worker_build="test", limit=1)
    for candidate in (previous, current):
        await seal_module._expire(service.runtime, candidate.thread_id, candidate.run_id, candidate.attempt_id)
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, current.run_id)
        attempt = await session.get_one(AttemptRow, current.attempt_id)
        assert run.status == "running" and run.current_attempt_id == current.attempt_id
        assert attempt.status == "leased" and attempt.finished_at is None


async def test_cancelled_recovery_rolls_back_and_scan_progress_survives_the_pass(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    leases = await _expired(service, scripted_model, runs_kit, 2)
    changed = asyncio.Event()
    recover = seal_module.recover

    async def pause_before_commit(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        await recover(*args, **kwargs)
        changed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(seal_module, "recover", pause_before_commit)
    sweep = LeaseExpirer(service.runtime, batch=1)
    task = asyncio.create_task(sweep())
    try:
        async with asyncio.timeout(5):
            await changed.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert set((await _statuses(service)).values()) == {"running"}
    async with transaction(service.runtime.storage) as session:
        attempt = await session.get_one(AttemptRow, leases[0].attempt_id)
        assert attempt.status == "leased" and attempt.finished_at is None and attempt.failure is None
    monkeypatch.setattr(seal_module, "recover", recover)
    await sweep()
    states = await _statuses(service)
    assert [states[lease.run_id] for lease in leases] == ["running", "accepted"]
    # Restarting loses only scan progress: durable evidence rediscovers the rolled-back recovery.
    await LeaseExpirer(service.runtime, batch=1)()
    assert set((await _statuses(service)).values()) == {"accepted"}
