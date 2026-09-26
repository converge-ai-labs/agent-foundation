"""Shared delivery policies and settlement-based retention across kinds."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra import outbox
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.outbox import Delivery, OutboxRow, Policy, claim, enqueue, purge_settled, settle
from a13n_service.runs.backlog import BacklogReporter
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


async def test_retention_uses_settlement_and_never_purges_pending(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    current = datetime.now(UTC)
    ids = {}
    async with transaction(runtime.storage) as session:
        for name, kind, status, age in (
            ("old-success", "email", "delivered", 10),
            ("new-success", "email", "delivered", 0),
            ("old-dead", "email", "dead", 10),
            ("kept-dead", "email", "dead", 2),
            ("pending", "email", "pending", 100),
            ("override-keeps", "webhook", "delivered", 10),
        ):
            identity = enqueue(
                session,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                kind=kind,
                target={},
                payload={},
            )
            await session.flush()
            row = await session.get_one(OutboxRow, identity)
            row.created_at = current - timedelta(days=100)
            row.status = status
            row.settled_at = current - timedelta(days=age) if status != "pending" else None
            row.delivered_at = row.settled_at if status == "delivered" else None
            ids[name] = identity
    policies = {
        "email": Policy(delivered_retention_seconds=86400, dead_retention_seconds=7 * 86400),
        "webhook": Policy(delivered_retention_seconds=30 * 86400),
    }
    assert await purge_settled(runtime.storage, policies=policies, limit=1, budget_seconds=5) == 2
    async with short_session(runtime.storage) as session:
        remaining = set(await session.scalars(select(OutboxRow.id)))
    assert remaining == {value for name, value in ids.items() if name not in {"old-success", "old-dead"}}


async def test_each_kind_uses_its_own_attempt_and_concurrency_policy(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    async with transaction(runtime.storage) as session:
        for kind in ("email", "webhook"):
            for _ in range(3):
                enqueue(
                    session,
                    organization_id=tenant.organization_id,
                    workspace_id=tenant.workspace_id,
                    kind=kind,
                    target={},
                    payload={},
                )
    active = {"email": 0, "webhook": 0}
    peak = dict(active)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fail(claimed) -> None:  # type: ignore[no-untyped-def]
        active[claimed.kind] += 1
        peak[claimed.kind] = max(peak[claimed.kind], active[claimed.kind])
        if active["email"] == 1 and active["webhook"] == 2:
            entered.set()
        try:
            await release.wait()
            raise ValueError("private detail")
        finally:
            active[claimed.kind] -= 1

    delivery = Delivery(
        runtime.storage,
        {"email": fail, "webhook": fail},
        owner="test",
        policies={
            "email": Policy(batch=1, parallel=1, max_attempts=1),
            "webhook": Policy(batch=2, parallel=2, max_attempts=2),
        },
    )
    task = asyncio.create_task(delivery())
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        release.set()
        await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert peak == {"email": 1, "webhook": 2}
    async with short_session(runtime.storage) as session:
        attempted = list(await session.scalars(select(OutboxRow).where(OutboxRow.attempts > 0)))
        assert len(attempted) == 3
        assert {row.status for row in attempted if row.kind == "email"} == {"dead"}
        assert {row.status for row in attempted if row.kind == "webhook"} == {"pending"}
        assert {row.last_error for row in attempted} == {"ValueError"}


async def test_dead_at_enqueue_and_expired_final_claim_get_settlement_timestamps(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    async with transaction(runtime.storage) as session:
        dead = enqueue(
            session,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            kind="email",
            target={},
            payload={},
            dead="unusable",
        )
        pending = enqueue(
            session,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            kind="email",
            target={},
            payload={},
        )
    (first,) = await claim(runtime.storage, "email", owner="one", limit=1, lease_seconds=60, max_attempts=1)
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(OutboxRow).where(OutboxRow.id == pending).values(lease_expires_at=datetime.now(UTC))
        )
    assert await claim(runtime.storage, "email", owner="two", limit=1, lease_seconds=60, max_attempts=1) == []
    async with transaction(runtime.storage) as session:
        assert not await settle(session, first, "delivered")
        for identity in (dead, pending):
            row = await session.get_one(OutboxRow, identity)
            assert row.status == "dead" and row.settled_at is not None


async def test_backlog_alert_requires_sustained_threshold_and_recovers(runtime, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs import backlog

    current = datetime.now(UTC)
    size = 2

    async def values(*args):  # type: ignore[no-untyped-def]
        return {"email": (size, 0)}

    async def now(session):  # type: ignore[no-untyped-def]
        return current

    monkeypatch.setattr(backlog, "report_backlog", values)
    monkeypatch.setattr(backlog, "now", now)
    reporter = BacklogReporter(runtime.storage, {"email": Policy(backlog_count=2, backlog_alert_seconds=300)})
    await reporter()
    assert not reporter.alerting
    current += timedelta(seconds=299)
    await reporter()
    assert not reporter.alerting
    current += timedelta(seconds=1)
    await reporter()
    assert reporter.alerting == {"email"}
    size = 0
    await reporter()
    assert not reporter.alerting and not reporter.since
    async with transaction(runtime.storage) as session:
        enqueue(
            session,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            kind="email",
            target={},
            payload={},
            dead="unusable",
        )
    await reporter()
    assert reporter.dead == {"email"}


@pytest.mark.parametrize("stage", ["claim", "handler"])
async def test_stopping_delivery_allows_inflight_io_to_finish_cancellation_cleanup(runtime, monkeypatch, stage) -> None:  # type: ignore[no-untyped-def]
    async with transaction(runtime.storage) as session:
        enqueue(session, organization_id=None, workspace_id=None, kind="email", target={}, payload={})
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def interrupted(*args, **kwargs):  # type: ignore[no-untyped-def]
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Database drivers await their server-side query cancellation before unwinding.
            # A second cancellation here interrupts that cleanup and can corrupt a pipeline.
            await asyncio.sleep(0)
            cleaned.set()

    if stage == "claim":
        monkeypatch.setattr(outbox, "claim", interrupted)
    task = asyncio.create_task(
        Delivery(runtime.storage, {"email": interrupted}, owner="test", policies={"email": Policy()})()
    )
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_delivery_deadline_allows_cleanup_and_schedules_a_retry(runtime) -> None:  # type: ignore[no-untyped-def]
    async with transaction(runtime.storage) as session:
        identity = enqueue(session, organization_id=None, workspace_id=None, kind="email", target={}, payload={})
    cleaned = asyncio.Event()

    async def interrupted(claimed):  # type: ignore[no-untyped-def]
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    # Shorten only this test's clock; deployment settings retain their minimum lease.
    policy = Policy(batch=1).model_copy(update={"lease_seconds": 0.02})
    async with asyncio.timeout(5):
        await Delivery(runtime.storage, {"email": interrupted}, owner="test", policies={"email": policy})()
    assert cleaned.is_set()
    async with short_session(runtime.storage) as session:
        row = await session.get_one(OutboxRow, identity)
        assert (row.status, row.attempts, row.last_error) == ("pending", 1, "TimeoutError")
        assert row.lease_owner is None


@pytest.mark.parametrize("own_deadline", [True, False])
async def test_purge_budget_allows_cleanup_and_only_suppresses_its_own_timeout(
    runtime, monkeypatch, own_deadline
) -> None:  # type: ignore[no-untyped-def]
    cleaned = asyncio.Event()
    calls = 0

    async def batch(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            return 3
        try:
            if not own_deadline:
                raise TimeoutError("backend deadline")
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    monkeypatch.setattr(outbox, "_purge_batch", batch)
    work = purge_settled(runtime.storage, policies={"email": Policy()}, limit=5, budget_seconds=0.02)
    async with asyncio.timeout(5):
        if own_deadline:
            assert await work == 3
        else:
            with pytest.raises(TimeoutError, match="backend deadline"):
                await work
    assert cleaned.is_set()
