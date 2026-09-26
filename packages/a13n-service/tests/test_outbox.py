"""Shared delivery policies and settlement-based retention across kinds."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
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
