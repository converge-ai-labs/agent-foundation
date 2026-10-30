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
        assert (await heartbeat(service.runtime, lease.worker_id))[0] == ((lease.thread_id, record.execution_id),)
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
        assert (await heartbeat(service.runtime, origin.worker_id))[0] == ()
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


async def test_archive_cancels_old_and_current_runs_and_rejects_late_admission(service, scripted_model, runs_kit):
    from a13n_service.runs.tables import ToolExecutionRow

    started, origin = await _origin(service, scripted_model, runs_kit)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()

    old = _admit(service.runtime, origin, operation)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        scripted_model.say("Origin completed")
        await execute(service.runtime, origin, AttemptControl())
        submitted = await runs_kit.submit(
            service, origin.thread_id, runs_kit.message({"id": started["run"]["agent_id"]}, "next")
        )
        assert submitted.status_code == 201, submitted.text
        (successor,) = await claim(service.runtime, worker_id="worker-a", worker_build="test", limit=1)
        entered.clear()
        current = _admit(service.runtime, successor, operation)
        await asyncio.wait_for(entered.wait(), 5)
        thread = await runs_kit.get_thread(service, origin.thread_id)
        archived = await service.client.post(
            f"{service.api}/threads/{origin.thread_id}/archive", headers=runs_kit.if_match(thread)
        )
        assert archived.status_code == 200, archived.text
        cancellations, renewed = await heartbeat(service.runtime, origin.worker_id)
        assert set(cancellations) == {(origin.thread_id, old.execution_id), (origin.thread_id, current.execution_id)}
        assert renewed == {old.execution_id, current.execution_id}
        late = _admit(service.runtime, successor, operation)
        result = await service.runtime.executions.wait(origin.thread_id, late.execution_id)
        assert result.record.status == "failed" and service.runtime.executions.accepting
        async with transaction(service.runtime.storage) as session:
            assert await session.get(ToolExecutionRow, late.execution_id) is None
        for thread_id, execution_id in cancellations:
            service.runtime.executions.request_cancellation(thread_id, execution_id)
        await service.runtime.executions.join()
        assert (
            await service.runtime.executions.result(origin.thread_id, old.execution_id)
        ).record.status == "cancelled"
    finally:
        finish.set()
        await service.runtime.executions.close()


@pytest.mark.parametrize("retry", [False, True])
async def test_large_failure_hint_delivers_without_copying_error(service, scripted_model, runs_kit, retry):
    from pydantic_ai.exceptions import ModelRetry, ToolFailed

    _, lease = await _origin(service, scripted_model, runs_kit)
    message = "failure" * service.runtime.settings.control.inbox_bytes

    async def operation():
        raise ModelRetry(message) if retry else ToolFailed(message)

    record = _admit(service.runtime, lease, operation)
    await service.runtime.executions.join()
    scripted_model.say("Origin finished")
    await execute(service.runtime, lease, AttemptControl())
    assert len(await _deliver(service.runtime)) == 1
    hints = [entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "tool_result"]
    assert len(hints) == 1
    assert hints[0]["payload"] == {"execution_id": record.execution_id, "tool_name": "slow", "status": "failed"}
    result = await service.runtime.executions.result(lease.thread_id, record.execution_id)
    assert result.record.error == message
    assert result.retry_prompt == (message if retry else None)


async def test_failed_terminal_publication_is_not_renewed(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs.tables import ToolExecutionRow

    _, lease = await _origin(service, scripted_model, runs_kit)
    owner = service.runtime.executions
    entered, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()
        return "original"

    async def unavailable(*args, **kwargs):
        raise OSError("object store unavailable")

    record = _admit(service.runtime, lease, operation)
    await asyncio.wait_for(entered.wait(), 5)
    monkeypatch.setattr(service.runtime.objects, "put", unavailable)
    finish.set()
    await owner.join()
    assert not owner.accepting and not owner.active
    async with transaction(service.runtime.storage) as session:
        before = (await session.get_one(ToolExecutionRow, record.execution_id)).lease_expires_at
    assert await heartbeat(service.runtime, lease.worker_id) == ((), set())
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(ToolExecutionRow, record.execution_id)).lease_expires_at == before
    await owner.close()


async def test_retained_cross_worker_call_stays_queued_until_barrier_clears(service, scripted_model, runs_kit):
    started, lease = await _origin(service, scripted_model, runs_kit)
    entered, finish, next_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second = replace(service.runtime)

    async def first_operation():
        entered.set()
        await finish.wait()

    async def next_operation():
        next_entered.set()
        return "next"

    _admit(service.runtime, lease, first_operation, sequential=True)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        scripted_model.say("Origin completed")
        await execute(service.runtime, lease, AttemptControl())
        submitted = await runs_kit.submit(
            service, lease.thread_id, runs_kit.message({"id": started["run"]["agent_id"]}, "next")
        )
        assert submitted.status_code == 201, submitted.text
        (successor,) = await claim(second, worker_id="worker-b", worker_build="test", limit=1)
        record = _admit(second, successor, next_operation)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(next_entered.wait(), 0.2)
        assert (await second.executions.info(lease.thread_id, record.execution_id)).status == "queued"
        assert (await Store(second).get(lease.thread_id, record.execution_id)).record.status == "queued"
        finish.set()
        await service.runtime.executions.join()
        await asyncio.wait_for(second.executions.join(), 5)
        assert next_entered.is_set()
        assert (await second.executions.result(lease.thread_id, record.execution_id)).result == "next"
    finally:
        finish.set()
        await service.runtime.executions.close()
        await second.executions.close()


async def test_cleanup_after_revocation_keeps_identity_and_expiry_fences(service, scripted_model, runs_kit):
    from datetime import timedelta

    from a13n_harness.environment.models import EnvironmentAction
    from a13n_harness.tool_execution import execution_cleanup
    from a13n_service.runs.attempts import AuthorityRevoked, LeaseLost
    from a13n_service.runs.desktop_assistance.admission import operation as guard
    from a13n_service.runs.tables import ToolExecutionRow

    from .test_platform_tools import set_role

    _, lease = await _origin(service, scripted_model, runs_kit)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()
        with execution_cleanup():
            for action in (
                EnvironmentAction.PROCESS_KILL,
                EnvironmentAction.PROCESS_WAIT,
                EnvironmentAction.PROCESS_RELEASE,
            ):
                async with guard(service.runtime, lease, "test-environment", None, action):
                    pass
            with pytest.raises(AuthorityRevoked):
                async with guard(service.runtime, lease, "test-environment", None, EnvironmentAction.PROCESS_START):
                    pytest.fail("revocation must still block new effects")
            with pytest.raises(LeaseLost):
                async with guard(
                    service.runtime,
                    replace(lease, token="wrong-owner"),
                    "test-environment",
                    None,
                    EnvironmentAction.PROCESS_KILL,
                ):
                    pytest.fail("cleanup must retain token fencing")
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ToolExecutionRow, record.execution_id)
                row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            assert await heartbeat(service.runtime, lease.worker_id) == (
                ((lease.thread_id, record.execution_id),),
                set(),
            )
            with pytest.raises(LeaseLost):
                async with guard(service.runtime, lease, "test-environment", None, EnvironmentAction.PROCESS_KILL):
                    pytest.fail("cleanup must retain expiry fencing")
            # Restore test ownership only so the invocation can publish its assertions.
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(ToolExecutionRow, record.execution_id)
                row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
        return "verified"

    record = _admit(service.runtime, lease, operation)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with transaction(service.runtime.storage) as session:
            await set_role(session, service, "viewer")
        finish.set()
        await asyncio.wait_for(service.runtime.executions.join(), 5)
        assert (await service.runtime.executions.result(lease.thread_id, record.execution_id)).result == "verified"
    finally:
        finish.set()
        await service.runtime.executions.close()
