from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.subagents.cancellation import ChildCancellationReconciler
from a13n_service.subagents.maintenance import SubagentMaintenance
from a13n_service.subagents.results import AsyncSubagentResultPublisher
from a13n_service.subagents.successors import AsyncSubagentSuccessorReconciler
from anyio import Event, create_task_group, fail_after, sleep

pytestmark = pytest.mark.anyio


async def test_drain_finishes_the_active_batch_without_starting_another():
    entered = Event()
    finish = Event()
    trace = []

    async def cancel_children():
        entered.set()
        await finish.wait()
        trace.append("cancelled")

    cancellation = Mock(spec=ChildCancellationReconciler)
    cancellation.reconcile_once = AsyncMock(side_effect=cancel_children)
    results = Mock(spec=AsyncSubagentResultPublisher)
    results.reconcile_once = AsyncMock(side_effect=lambda: trace.append("published"))
    successors = Mock(spec=AsyncSubagentSuccessorReconciler)
    successors.reconcile_once = AsyncMock(side_effect=lambda: trace.append("accepted"))
    maintenance = SubagentMaintenance(cancellation, results, successors, poll_interval_seconds=0.001)
    with fail_after(1):
        async with create_task_group() as tasks:
            tasks.start_soon(maintenance.run)
            await entered.wait()
            maintenance.drain()
            await sleep(0.01)
            assert trace == []
            finish.set()
            await maintenance.wait_stopped()
    assert trace == ["cancelled", "published", "accepted"]
    cancellation.reconcile_once.assert_awaited_once()
