"""Membership hints change claim sizes, never PostgreSQL ownership or lease recovery."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call
from uuid import uuid4

import pytest
from a13n_service.infra.db import now, transaction
from a13n_service.runs import worker
from a13n_service.runs.claim import claim
from a13n_service.runs.members import Members
from a13n_service.runs.tables import RunRow
from redis.asyncio import Redis
from sqlalchemy import update

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("enabled", [True, False])
async def test_worker_reports_realtime_slots_separately(monkeypatch, enabled):
    ordinary, realtime = Mock(), Mock()
    monkeypatch.setattr(worker, "SLOTS", ordinary)
    monkeypatch.setattr(worker, "REALTIME_SLOTS", realtime)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(worker=SimpleNamespace(slots=4, realtime_slots=8)),
        realtime_bridge=object() if enabled else None,
    )
    instance = worker.Worker(runtime, Mock())
    instance.free -= 1
    instance.realtime_free -= int(enabled)
    instance._report_slots()
    assert ordinary.set.call_args_list == [call(3, {"state": "free"}), call(1, {"state": "busy"})]
    assert realtime.set.call_args_list == [
        call(7 if enabled else 0, {"state": "free"}),
        call(int(enabled), {"state": "busy"}),
    ]


async def test_join_leave_restart_and_scope_isolation(redis_url):
    async with Redis.from_url(redis_url) as redis:
        scope = uuid4().hex
        first = Members(redis, scope, "old-incarnation", ttl=10, timeout=1)
        second = Members(redis, scope, "peer", ttl=10, timeout=1)
        replacement = Members(redis, scope, "new-incarnation", ttl=10, timeout=1)
        isolated = Members(redis, scope + "other-schema", "other", ttl=10, timeout=1)
        try:
            assert await first.refresh() == 1
            assert await second.refresh() == 2
            assert await first.refresh() == 2  # Repeated heartbeats do not add members.
            assert await isolated.refresh() == 1
            assert await replacement.refresh() == 3
            await first.leave()
            assert await replacement.refresh() == 2  # Old exit cannot remove its replacement.
            await second.leave()
            assert await replacement.refresh() == 1
            assert 0 < await redis.pttl(replacement.key) <= 10000
        finally:
            for member in (first, second, replacement, isolated):
                await member.leave()
        assert not await redis.exists(first.key)


async def test_crashed_member_expires_without_exit(redis_url):
    async with Redis.from_url(redis_url) as redis:
        scope = uuid4().hex
        crashed = Members(redis, scope, "crashed", ttl=0.08, timeout=1)
        survivor = Members(redis, scope, "survivor", ttl=5, timeout=1)
        assert await crashed.refresh() == 1
        assert await survivor.refresh() == 2
        try:
            async with asyncio.timeout(2):
                while await survivor.refresh() != 1:
                    await asyncio.sleep(0.01)
            assert await redis.zscore(survivor.key, crashed.worker_id) is None
        finally:
            await survivor.leave()


async def test_redis_unavailable_returns_unknown_not_single_worker():
    async with Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.05, socket_timeout=0.05) as redis:
        member = Members(redis, uuid4().hex, "worker", ttl=10, timeout=0.1)
        assert await member.refresh() is None
        await member.leave()  # Dependency failure cannot block draining.


async def test_membership_deadline_and_recovery(redis_url, monkeypatch):
    async with Redis.from_url(redis_url) as redis:
        member = Members(redis, uuid4().hex, "worker", ttl=10, timeout=0.02)
        original = redis.eval

        async def stalled(*args):
            await asyncio.Event().wait()

        monkeypatch.setattr(redis, "eval", stalled)
        assert await member.refresh() is None
        monkeypatch.setattr(redis, "eval", original)
        member.timeout = 1  # Recovery tests connectivity, not a 20 ms host scheduling deadline.
        try:
            assert await member.refresh() == 1
        finally:
            await member.leave()


async def test_member_changes_adjust_shares_without_reclaiming_active_attempts(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    queued = [await runs_kit.start_thread(service, agent, f"task {i}") for i in range(12)]
    future_id = queued[-1]["run"]["id"]
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        await session.execute(
            update(RunRow).where(RunRow.id == future_id).values(available_at=current + timedelta(days=1))
        )
    redis = service.runtime.redis
    scope = uuid4().hex
    members = [Members(redis, scope, f"worker-{i}", ttl=10, timeout=1) for i in range(4)]
    try:
        for member in members:
            await member.refresh()
        first = await claim(
            service.runtime, worker_id="worker-0", worker_build="test", limit=128, members=await members[0].refresh()
        )
        assert len(first) == 3  # ceil(11 due / 4), not the full local slot count.
        await members[2].leave()
        await members[3].leave()
        second = await claim(
            service.runtime, worker_id="worker-1", worker_build="test", limit=128, members=await members[1].refresh()
        )
        assert len(second) == 4  # ceil(8 remaining / 2).
        await members[0].leave()
        third = await claim(
            service.runtime, worker_id="worker-1", worker_build="test", limit=128, members=await members[1].refresh()
        )
        assert len(third) == 4
        leases = first + second + third
        assert len({lease.run_id for lease in leases}) == 11
        assert future_id not in {lease.run_id for lease in leases}
        # Leaving membership does not expire/reassign any of worker-0's attempts.
        assert await claim(service.runtime, worker_id="worker-1", worker_build="test", limit=128) == []
    finally:
        for member in members:
            await member.leave()


@pytest.mark.parametrize("count,expected_limit", [(None, 8), (4, 128), (1, 128)])
async def test_worker_uses_member_count_and_waits_between_shared_batches(service, monkeypatch, count, expected_limit):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service.runs import worker as module

    original = service.runtime
    runtime = SimpleNamespace(
        storage=original.storage,
        redis=original.redis,
        executions=original.executions,
        realtime_bridge=original.realtime_bridge,
        settings=original.settings.model_copy(
            update={
                "worker": original.settings.worker.model_copy(
                    update={"slots": 128, "realtime_slots": 12, "scan_seconds": 0.05}
                )
            }
        ),
    )
    membership = SimpleNamespace(refresh=AsyncMock(return_value=count), leave=AsyncMock())
    monkeypatch.setattr(module, "Members", lambda *args, **kwargs: membership)
    reached = asyncio.Event()
    calls = []

    async def idle(*args):
        await asyncio.Event().wait()

    async def take(runtime, **kwargs):
        calls.append((asyncio.get_running_loop().time(), kwargs))
        if len(calls) == 3:
            reached.set()
        # Single-worker full batches may immediately refill; all other successful
        # batches must wait even when there are free slots and wake markers.
        return [
            SimpleNamespace(attempt_id=f"attempt-{len(calls)}-{i}", realtime=kwargs["realtime"])
            for i in range(kwargs["limit"])
        ]

    worker = module.Worker(runtime, None)
    monkeypatch.setattr(module, "claim", take)
    monkeypatch.setattr(worker, "_supervise", idle)
    monkeypatch.setattr(worker, "_recover_shells", idle)
    monkeypatch.setattr(runtime.executions.routing, "run", idle)
    monkeypatch.setattr(runtime.executions.output, "run", idle)

    async def finish(lease, control):
        worker.running.pop(lease.attempt_id)
        if lease.realtime:
            worker.realtime_free += 1
        else:
            worker.free += 1
        worker.slot_released.set()

    async def drain():
        worker.stopping = True
        await membership.leave()

    monkeypatch.setattr(worker, "_attempt", finish)
    monkeypatch.setattr(worker, "_drain", drain)
    task = asyncio.create_task(worker._run())
    try:
        async with asyncio.timeout(3):
            await reached.wait()
        assert [(call["realtime"], call["limit"], call["members"]) for _, call in calls[:3]] == [
            (False, expected_limit, count if count is not None else 1),
            (True, 8 if count is None else 12, count if count is not None else 1),
            (False, expected_limit, count if count is not None else 1),
        ]
        if count != 1:
            assert calls[2][0] - calls[1][0] >= runtime.settings.worker.scan_seconds
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert membership.leave.await_count >= 1
