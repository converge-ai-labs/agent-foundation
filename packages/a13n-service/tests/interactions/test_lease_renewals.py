"""Batch scheduling retains independent timeout, failure, and local lease state."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService
from a13n_service.interactions.lease_renewals import LeaseRenewalBatcher
from a13n_service.interactions.run_control import RunAttemptControl
from anyio import CancelScope, Event, create_task_group, fail_after

from .conftest import NOW
from .test_attempt_executor import _context, _receipt

pytestmark = pytest.mark.anyio


def _contexts(count):
    return [replace(_context(f"thread-{index}"), run_attempt_id=f"attempt-{index}") for index in range(count)]


def _control(context, execution):
    return RunAttemptControl(context=context, execution=execution, states=Mock(), inbox=Mock())


async def test_valid_monitors_share_one_batch_and_a_stale_sibling_fails_independently():
    contexts = _contexts(3)
    execution = Mock(spec=AttemptExecutionService)
    execution.heartbeat_many = AsyncMock(return_value={item.run_attempt_id: _receipt(item) for item in contexts[1:]})
    batcher = LeaseRenewalBatcher(execution)
    failures = []

    async def renew(context):
        try:
            await _control(context, execution).renew_lease(batcher.renew)
        except AttemptAuthorityError:
            failures.append(context.run_attempt_id)

    with fail_after(2):
        async with batcher.open(), create_task_group() as tasks:
            for context in contexts:
                tasks.start_soon(renew, context)
    assert execution.heartbeat_many.await_count == 1
    assert failures == [contexts[0].run_attempt_id]
    with pytest.raises(AttemptAuthorityError):
        contexts[0].lease.require_current(NOW)
    assert all(item.lease.expires_at == _receipt(item).lease_expires_at for item in contexts[1:])
    execution.heartbeat.assert_not_called()


async def test_batches_are_bounded_and_empty_worker_does_not_write():
    contexts = _contexts(129)
    execution = Mock(spec=AttemptExecutionService)

    async def heartbeat_many(items):
        return {item.run_attempt_id: _receipt(item) for item in items}

    execution.heartbeat_many = AsyncMock(side_effect=heartbeat_many)
    batcher = LeaseRenewalBatcher(execution)
    with fail_after(2):
        async with batcher.open(), create_task_group() as tasks:
            execution.heartbeat_many.assert_not_called()
            for context in contexts:
                tasks.start_soon(batcher.renew, context)
    batches = [call.args[0] for call in execution.heartbeat_many.await_args_list]
    assert all(1 <= len(batch) <= 128 for batch in batches)
    assert sorted(item.run_attempt_id for batch in batches for item in batch) == sorted(
        item.run_attempt_id for item in contexts
    )


async def test_cancelled_monitor_cannot_receive_late_success_and_sibling_still_renews():
    contexts = _contexts(2)
    entered, finish = Event(), Event()
    cancelled, completed = Event(), Event()
    execution = Mock(spec=AttemptExecutionService)

    async def heartbeat_many(items):
        entered.set()
        await finish.wait()
        return {item.run_attempt_id: _receipt(item) for item in items}

    execution.heartbeat_many = AsyncMock(side_effect=heartbeat_many)
    batcher = LeaseRenewalBatcher(execution)
    cancellation = CancelScope()

    async def abandon():
        with cancellation:
            await _control(contexts[0], execution).renew_lease(batcher.renew)
        cancelled.set()

    async def keep_renewing():
        await _control(contexts[1], execution).renew_lease(batcher.renew)
        completed.set()

    with fail_after(2):
        async with batcher.open(), create_task_group() as tasks:
            tasks.start_soon(abandon)
            tasks.start_soon(keep_renewing)
            await entered.wait()
            cancellation.cancel()
            await cancelled.wait()
            finish.set()
            await completed.wait()
    assert contexts[0].lease.expires_at == NOW + timedelta(seconds=30)
    with pytest.raises(AttemptAuthorityError):
        contexts[0].lease.require_current(NOW)
    assert contexts[1].lease.expires_at == _receipt(contexts[1]).lease_expires_at


async def test_database_failure_fails_waiters_without_stopping_later_batches():
    contexts = _contexts(2)
    execution = Mock(spec=AttemptExecutionService)
    execution.heartbeat_many = AsyncMock(
        side_effect=[ConnectionError("unknown commit"), {contexts[1].run_attempt_id: _receipt(contexts[1])}]
    )
    batcher = LeaseRenewalBatcher(execution)
    with fail_after(2):
        async with batcher.open():
            with pytest.raises(AttemptAuthorityError):
                await _control(contexts[0], execution).renew_lease(batcher.renew)
            await _control(contexts[1], execution).renew_lease(batcher.renew)
    assert contexts[0].lease.expires_at == NOW + timedelta(seconds=30)
    with pytest.raises(AttemptAuthorityError):
        contexts[0].lease.require_current(NOW)
    assert contexts[1].lease.expires_at == _receipt(contexts[1]).lease_expires_at


async def test_shutdown_rejects_unconfirmed_inflight_and_new_requests():
    context = _contexts(1)[0]
    entered, rejected = Event(), Event()
    execution = Mock(spec=AttemptExecutionService)

    async def blocked(items):
        entered.set()
        await Event().wait()

    execution.heartbeat_many = AsyncMock(side_effect=blocked)
    batcher = LeaseRenewalBatcher(execution)

    async def renew():
        with pytest.raises(AttemptAuthorityError):
            await _control(context, execution).renew_lease(batcher.renew)
        rejected.set()

    with fail_after(2):
        async with create_task_group() as tasks:
            async with batcher.open():
                tasks.start_soon(renew)
                await entered.wait()
            await rejected.wait()
    with pytest.raises(AttemptAuthorityError):
        await batcher.renew(context)
    with pytest.raises(AttemptAuthorityError):
        context.lease.require_current(NOW)


async def test_lease_monitor_batches_on_timer_and_fences_on_timeout():
    from a13n_service.interactions.attempt_executor import LeaseMonitor

    context = replace(
        _contexts(1)[0], renewal_interval=timedelta(seconds=0.03), renewal_timeout=timedelta(seconds=0.05)
    )
    entered = Event()
    execution = Mock(spec=AttemptExecutionService)

    async def blocked(items):
        entered.set()
        await Event().wait()

    execution.heartbeat_many = AsyncMock(side_effect=blocked)
    control = _control(context, execution)
    cancel_executor = Mock()
    control.bind_executor(AsyncMock(), cancel_executor)
    batcher = LeaseRenewalBatcher(execution)
    with fail_after(2):
        async with batcher.open():
            with pytest.raises((TimeoutError, AttemptAuthorityError)):
                await LeaseMonitor(context, control, batcher).run()
    assert entered.is_set()
    cancel_executor.assert_called_once()
    assert context.lease.expires_at == NOW + timedelta(seconds=30)
    with pytest.raises(AttemptAuthorityError):
        context.lease.require_current(NOW)
