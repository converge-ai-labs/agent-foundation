"""Batch claims retain ownership, atomic publication and bounded database work."""

import asyncio
import importlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace

import pytest
from a13n_service.infra import outbox
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs.claim import claim
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.seal import release_attempt
from a13n_service.runs.tables import AttemptRow, RunItemPageRow, RunRow, ThreadRow
from sqlalchemy import event, select, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


@contextmanager
def statements(runtime: Runtime, *, timings: list[float] | None = None) -> Iterator[list[str]]:
    """Count this task's cursor executions, expanding executemany parameter groups.

    Fixture setup, transaction control, pool probes and server-side trigger queries are excluded.
    This is not a measurement of network round trips.
    """
    recorded: list[str] = []
    owner = asyncio.current_task()

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        if asyncio.current_task() is owner:
            if timings is not None:
                timings.append(perf_counter())
            # insertmanyvalues also sets executemany=True but sends one multi-row statement;
            # its flattened parameters are not separate executions.
            count = len(parameters) if context.execute_style.name == "EXECUTEMANY" else 1
            recorded.extend([statement] * count)

    engine = runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("count", [1, 4, 8, 128])
@pytest.mark.parametrize("subscribed", [False, True])
async def test_claim_sql_growth(service: SimpleNamespace, scripted_model, runs_kit: SimpleNamespace, count, subscribed):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    for index in range(count):
        await runs_kit.start_thread(service, agent, str(index))
    if subscribed:
        response = await service.client.post(
            f"{service.api}/subscriptions",
            json={
                "name": "Claims",
                "url": "http://127.0.0.1:9/hook",
                "kinds": ["run.running", "run_attempt.leased"],
                "signing_secret": "whsec_batch_claim_signing_secret",
            },
        )
        assert response.status_code == 201, response.text
    for recovery in (False, True):
        started = perf_counter()
        timings: list[float] = []
        with statements(service.runtime, timings=timings) as sql:
            leases = await claim(service.runtime, worker_id="worker-test", worker_build="test", limit=count)
        finished = perf_counter()
        assert len(leases) == count
        rows = (2 * count if subscribed else 0) + (count if recovery else 0)
        assert len(sql) == 6 + int(recovery) + (rows + 255) // 256
        assert len({lease.token for lease in leases}) == count
        async with short_session(service.runtime.storage) as session:
            attempts = (
                await session.scalars(
                    select(AttemptRow).where(AttemptRow.id.in_([lease.attempt_id for lease in leases]))
                )
            ).all()
            assert {a.lease_token_hash for a in attempts} == {secret_hash(lease.token) for lease in leases}
            assert all(a.number == (2 if recovery else 1) for a in attempts)
            assert all(a.start_reason == ("handoff" if recovery else "initial") for a in attempts)
            stored = (await session.scalars(select(RunRow))).all()
            assert all(r.attempts == 1 and r.status == "running" for r in stored)
            deliveries = (await session.scalars(select(OutboxRow).where(OutboxRow.kind == "webhook"))).all()
            assert all(row.payload["run"]["status"] == "running" for row in deliveries)

        print(
            f"claim count={count} subscribed={subscribed} recovery={recovery}: {len(sql)} SQL, "
            f"{(finished - started) * 1000:.1f} ms total, "
            f"{(finished - timings[1]) * 1000:.1f} ms candidate query through commit"
        )
        if not recovery:
            for lease in leases:
                await release_attempt(service.runtime, lease, status="yielded", yield_reason="drain")


async def test_empty_claim_only_reads_time_and_candidates(service, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    with statements(service.runtime) as sql:
        assert await claim(service.runtime, worker_id="empty", worker_build="test", limit=8) == []
    assert len(sql) == 2


async def test_mixed_initial_recovery_and_handoff_preserve_numbers_and_cleanup(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    for name in ("recovery", "handoff"):
        await runs_kit.start_thread(service, agent, name)
    recovered, yielded = await claim(runtime, worker_id="before", worker_build="test", limit=2)
    await release_attempt(runtime, recovered, status="failed")
    # More handoffs than charged attempts: the next number must come from history.
    for _ in range(2):
        await release_attempt(runtime, yielded, status="yielded", yield_reason="drain")
        [yielded] = await claim(runtime, worker_id="handoff", worker_build="test", limit=1)
    await release_attempt(runtime, yielded, status="yielded", yield_reason="drain")
    await runs_kit.start_thread(service, agent, "initial")
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(RunRow)
            .where(RunRow.id == recovered.run_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        before = {r.id: r.started_at for r in (await session.scalars(select(RunRow))).all()}
        for lease in (recovered, yielded):
            session.add(
                RunItemPageRow(
                    run_id=lease.run_id,
                    organization_id=lease.organization_id,
                    workspace_id=lease.workspace_id,
                    first_ordinal=1,
                    last_ordinal=1,
                    key=f"page/{lease.run_id}",
                    digest="0" * 64,
                    size=0,
                )
            )
        old_outbox = set(await session.scalars(select(OutboxRow.id)))
    leases = await claim(runtime, worker_id="after", worker_build="test", limit=8)
    assert len(leases) == 3
    async with short_session(runtime.storage) as session:
        for lease in leases:
            run = await session.get_one(RunRow, lease.run_id)
            attempt = await session.get_one(AttemptRow, lease.attempt_id)
            if lease.run_id == recovered.run_id:
                assert (attempt.number, attempt.start_reason, run.attempts, attempt.replaces_attempt_id) == (
                    2,
                    "recovery",
                    2,
                    recovered.attempt_id,
                )
            elif lease.run_id == yielded.run_id:
                assert (attempt.number, attempt.start_reason, run.attempts, attempt.replaces_attempt_id) == (
                    4,
                    "handoff",
                    1,
                    yielded.attempt_id,
                )
            else:
                assert (attempt.number, attempt.start_reason, run.attempts, attempt.replaces_attempt_id) == (
                    1,
                    "initial",
                    1,
                    None,
                )
            assert run.started_at == (before[run.id] or attempt.heartbeat_at)
        cleanup = (
            await session.scalars(
                select(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup", OutboxRow.id.not_in(old_outbox))
            )
        ).all()
        assert {r.target["run_id"]: r.payload for r in cleanup} == {
            lease.run_id: {"keep": [f"page/{lease.run_id}"], "before_attempt": number, "after": None}
            for lease, number in ((recovered, 2), (yielded, 4))
        }


async def test_concurrent_batches_skip_locked_runs_without_locking_threads(
    service, scripted_model, runs_kit, monkeypatch
):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    for index in range(4):
        await runs_kit.start_thread(service, agent, str(index))
    async with short_session(runtime.storage) as session:
        ordered = list(await session.scalars(select(RunRow.id).order_by(RunRow.available_at, RunRow.id)))
    future = await runs_kit.start_thread(service, agent, "future")
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(RunRow)
            .where(RunRow.id == future["run"]["id"])
            .values(available_at=datetime.now(UTC) + timedelta(days=1))
        )
    module = importlib.import_module("a13n_service.runs.claim")
    original = module.prepare_webhooks
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def hold(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, "prepare_webhooks", hold)
    # Thread locks must not prevent claims; only run rows arbitrate ownership.
    async with transaction(runtime.storage) as session:
        await session.scalars(select(ThreadRow).with_for_update())
        first = asyncio.create_task(claim(runtime, worker_id="first", worker_build="test", limit=2))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
                second = await claim(runtime, worker_id="second", worker_build="test", limit=2)
            assert [lease.run_id for lease in second] == ordered[2:]
        finally:
            release.set()
            first_result = await first
    assert [lease.run_id for lease in first_result] == ordered[:2]
    assert await claim(runtime, worker_id="later", worker_build="test", limit=8) == []


@pytest.mark.parametrize("failure", ["late_chunk", "commit"])
async def test_claim_failure_rolls_back_the_entire_batch(
    service, scripted_model, runs_kit, monkeypatch, caplog, failure
):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    agent = await runs_kit.create_agent(service, scripted_model)
    for index in range(3):
        await runs_kit.start_thread(service, agent, str(index))
    response = await service.client.post(
        f"{service.api}/subscriptions",
        json={"name": "Rollback", "url": "http://127.0.0.1:9/hook", "kinds": ["run.running", "run_attempt.leased"]},
    )
    assert response.status_code == 201, response.text
    for lease in await claim(runtime, worker_id="before", worker_build="test", limit=3):
        await release_attempt(runtime, lease, status="yielded", yield_reason="drain")
    async with short_session(runtime.storage) as session:
        attempt_ids = set(await session.scalars(select(AttemptRow.id)))
        outbox_ids = set(await session.scalars(select(OutboxRow.id)))
    caplog.clear()
    caplog.set_level(logging.INFO)
    module = importlib.import_module("a13n_service.runs.claim")
    original = module.enqueue_batch

    async def fail(session, rows):
        if failure == "late_chunk":
            # First chunk reaches PostgreSQL; a duplicate in the next must undo it too.
            rows[-1].id = rows[0].id
        await original(session, rows)
        if failure == "commit":
            await session.execute(update(RunRow).values(current_attempt_id="rat_missing"))

    with monkeypatch.context() as patch:
        patch.setattr(module, "enqueue_batch", fail)
        patch.setattr(outbox, "_BATCH_ROWS", 1)
        with pytest.raises(DBAPIError):
            await claim(runtime, worker_id="failing", worker_build="test", limit=3)
    async with short_session(runtime.storage) as session:
        assert set(await session.scalars(select(AttemptRow.id))) == attempt_ids
        assert set(await session.scalars(select(OutboxRow.id))) == outbox_ids
        assert all(
            r.status == "accepted" and r.current_attempt_id is None for r in await session.scalars(select(RunRow))
        )
    assert not any(r.getMessage() == "Attempt claimed" for r in caplog.records)
    assert len(await claim(runtime, worker_id="retry", worker_build="test", limit=3)) == 3
