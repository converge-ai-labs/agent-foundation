"""Durable admission survives Attempt takeover and delayed accounting."""

import asyncio

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.claim import claim
from a13n_service.runs.request_budget import reserve
from a13n_service.runs.tables import ModelDispatchRow
from a13n_service.runs.usage import ingest_delta, totals
from pydantic_ai.exceptions import UsageLimitExceeded
from sqlalchemy import func, select

from .test_usage_deltas import delta

pytestmark = pytest.mark.anyio


async def test_concurrent_dispatch_and_late_report_share_one_ceiling(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)

    async def admit(call_id, *, continuation=False):
        async with transaction(service.runtime.storage) as session:
            await reserve(
                session, run_id=run_id, attempt_id=lease.attempt_id, call_id=call_id, limit=2, continuation=continuation
            )

    results = await asyncio.gather(*(admit(f"call_{i}") for i in range(3)), return_exceptions=True)
    assert sum(isinstance(value, UsageLimitExceeded) for value in results) == 1
    async with transaction(service.runtime.storage) as session:
        calls = list(await session.scalars(select(ModelDispatchRow.call_id)))
        assert len(calls) == 2
        assert (await totals(session, run_id))["requests"] == 0
    # Repeating admission after a lost acknowledgement is not another debit.
    await admit(calls[0])
    report = delta("late", 4, 1)
    report = report.model_copy(update={"records": (report.records[0].model_copy(update={"call_id": calls[0]}),)})
    await ingest_delta(service.runtime.storage, run_id, lease.attempt_id, report, {})
    with pytest.raises(UsageLimitExceeded):
        await admit("call_after_report")
    await admit("poll", continuation=True)
    async with transaction(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(ModelDispatchRow)) == 2
        assert (await totals(session, run_id))["requests"] == 1


async def test_refused_admission_rolls_back_occupancy(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    with pytest.raises(ValueError, match="policy"):
        async with transaction(service.runtime.storage) as session:
            await reserve(session, run_id=run_id, attempt_id=lease.attempt_id, call_id="refused", limit=1)
            raise ValueError("policy refused")
    async with transaction(service.runtime.storage) as session:
        await reserve(session, run_id=run_id, attempt_id=lease.attempt_id, call_id="allowed", limit=1)
    async with transaction(service.runtime.storage) as session:
        assert await session.get(ModelDispatchRow, "refused") is None
        assert await session.get(ModelDispatchRow, "allowed") is not None


async def test_takeover_counts_unreported_old_dispatches(service, scripted_model, runs_kit):
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow, RunRow
    from sqlalchemy import update

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    [old] = await claim(service.runtime, worker_id="old", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        await reserve(session, run_id=run_id, attempt_id=old.attempt_id, call_id="old_dispatch", limit=2)
        await session.execute(
            update(AttemptRow).where(AttemptRow.id == old.attempt_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))
    [new] = await claim(service.runtime, worker_id="new", worker_build="test", limit=1)
    async with transaction(service.runtime.storage) as session:
        await reserve(session, run_id=run_id, attempt_id=new.attempt_id, call_id="new_dispatch", limit=2)
    with pytest.raises(UsageLimitExceeded):
        async with transaction(service.runtime.storage) as session:
            await reserve(session, run_id=run_id, attempt_id=new.attempt_id, call_id="over_budget", limit=2)
    async with transaction(service.runtime.storage) as session:
        assert (await totals(session, run_id))["requests"] == 0
        assert await session.scalar(select(func.count()).select_from(ModelDispatchRow)) == 2
