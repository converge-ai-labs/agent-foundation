"""Retained executions obey Run-scoped Stop, mailbox capacity and cross-worker ordering."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from a13n_harness.tool_execution import StoredToolExecution
from a13n_service.infra import outbox
from a13n_service.infra.db import transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.tool_executions.authority import heartbeat
from a13n_service.runs.tool_executions.delivery import tool_results
from a13n_service.runs.tool_executions.store import ORIGIN, Store
from sqlalchemy import func, select

from .test_tool_execution_store import _deliver, _origin, _record, _save

pytestmark = pytest.mark.anyio


def _admit(runtime, lease, operation, *, sequential=False, release=None):
    token = ORIGIN.set(lease)
    try:
        record = runtime.executions.admit(
            operation,
            thread_id=lease.thread_id,
            run_id="harness-origin",
            tool_call_id="call-original",
            tool_name="slow",
            sequential=sequential,
            release=release,
        )
        runtime.executions.release_foreground(lease.thread_id, record.execution_id)
        return record
    finally:
        ORIGIN.reset(token)


async def test_stopped_origin_retains_cleanup_and_never_wakes(service, scripted_model, runs_kit):
    _, lease = await _origin(service, scripted_model, runs_kit)
    entered, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await asyncio.Event().wait()

    async def cleanup():
        cleaning.set()
        await finish.wait()

    record = _admit(service.runtime, lease, operation, release=cleanup)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        stopped = await service.client.post(f"{service.api}/runs/{lease.run_id}/interrupt")
        assert stopped.status_code == 200, stopped.text
        assert await heartbeat(service.runtime, lease.worker_id) == ((lease.thread_id, record.execution_id),)
        service.runtime.executions.request_cancellation(lease.thread_id, record.execution_id)
        await asyncio.wait_for(cleaning.wait(), 5)
        # Cancellation acceptance retains ownership until actual cleanup finishes.
        assert not (await service.runtime.executions.info(lease.thread_id, record.execution_id)).terminal
        control = AttemptControl()
        control.stopped.set()
        await execute(service.runtime, lease, control)
        finish.set()
        await service.runtime.executions.join()
        assert len(await _deliver(service.runtime)) == 1
        assert not [entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "tool_result"]
        assert (await runs_kit.get_thread(service, lease.thread_id))["current_run_id"] is None
        assert (
            await service.runtime.executions.result(lease.thread_id, record.execution_id)
        ).record.status == "cancelled"
    finally:
        finish.set()
        await service.runtime.executions.close()


@pytest.mark.parametrize("prior_sequential,next_sequential", [(True, False), (False, True), (False, False)])
async def test_cross_worker_barrier_and_later_stop_scope(
    service, scripted_model, runs_kit, prior_sequential, next_sequential
):
    started, origin = await _origin(service, scripted_model, runs_kit)
    entered, finish, next_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()
        return "original"

    record = _admit(service.runtime, origin, operation, sequential=prior_sequential)
    second = replace(service.runtime)
    pending = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        scripted_model.say("Retained")
        await execute(service.runtime, origin, AttemptControl())
        submitted = await runs_kit.submit(
            service,
            origin.thread_id,
            runs_kit.message({"id": started["run"]["agent_id"]}, "next"),
        )
        assert submitted.status_code == 201, submitted.text
        (successor,) = await claim(second, worker_id="worker-b", worker_build="test", limit=1)

        async def next_operation():
            next_entered.set()
            return "next"

        token = ORIGIN.set(successor)
        try:
            pending = asyncio.create_task(
                second.executions.inline(
                    next_operation,
                    thread_id=origin.thread_id,
                    sequential=next_sequential,
                )
            )
        finally:
            ORIGIN.reset(token)
        if prior_sequential or next_sequential:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(next_entered.wait(), 0.2)
        else:
            await asyncio.wait_for(next_entered.wait(), 5)
        stopped = await service.client.post(f"{service.api}/runs/{successor.run_id}/interrupt")
        assert stopped.status_code == 200, stopped.text
        assert await heartbeat(service.runtime, origin.worker_id) == ()
        assert (await service.runtime.executions.info(origin.thread_id, record.execution_id)).status == "running"
        finish.set()
        await service.runtime.executions.join()
        assert await asyncio.wait_for(pending, 5) == "next"
        assert (await second.executions.result(origin.thread_id, record.execution_id)).result == "original"
    finally:
        finish.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.runtime.executions.close()
        await second.executions.close()


async def test_full_inbox_defers_then_fenced_delivery_appends_once(serve, settings, scripted_model, runs_kit):
    settings = settings.model_copy(update={"control": settings.control.model_copy(update={"inbox_count": 1})})
    async with serve(settings=settings) as service:
        _, lease = await _origin(service, scripted_model, runs_kit)
        store, record = Store(service.runtime), _record(lease)
        await _save(store, lease, StoredToolExecution(record))
        complete = record.model_copy(
            update={
                "status": "succeeded",
                "completed_at": datetime.now(UTC),
                "result_available": True,
                "foreground_released": True,
            }
        )
        await _save(store, lease, StoredToolExecution(complete, "ready"))
        assert len(await _deliver(service.runtime)) == 1
        async with transaction(service.runtime.storage) as session:
            row = (await session.scalars(select(OutboxRow).where(OutboxRow.kind == "tool_result"))).one()
            assert (row.status, row.last_error, row.attempts) == ("pending", "inbox_full", 0)
            row.available_at = await session.scalar(select(func.now()))
        assert not (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
        scripted_model.say("Finished")
        await execute(service.runtime, lease, AttemptControl())
        (stale,) = await outbox.claim(
            service.runtime.storage, "tool_result", owner="stale", limit=1, lease_seconds=0, max_attempts=12
        )
        (current,) = await outbox.claim(
            service.runtime.storage, "tool_result", owner="current", limit=1, lease_seconds=60, max_attempts=12
        )
        deliver = tool_results(service.runtime)
        await deliver(stale)
        await deliver(current)
        await deliver(current)
        results = [entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "tool_result"]
        assert len(results) == 1
        assert (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
