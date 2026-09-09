from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.worker import WorkerExecutionLoop
from anyio import Event, create_task_group, fail_after

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("begin_without_waiting", [False, True])
async def test_worker_reserves_before_claim_releases_losers_and_drains_owned_roots(
    interaction_sessions,
    interaction_object_store,
    monkeypatch,
    begin_without_waiting,
):
    _, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    owned = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, acceptance._worker()
    )
    assert isinstance(owned, ClaimedAttempt)
    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("loser", run.id))
    runner = Mock()
    started, handed_off = Event(), Event()
    control = Mock(spec=RunAttemptControl)
    control.request_handoff = AsyncMock(side_effect=lambda reason: handed_off.set())
    loop = WorkerExecutionLoop(
        interaction_sessions,
        scheduler,
        HarnessPluginFactoryCatalog(()),
        runner,
        build_id="test",
        queue_name="default",
        concurrency=1,
        poll_seconds=0.01,
        drain_seconds=0.5,
    )
    monkeypatch.setattr(loop, "_organizations", AsyncMock(return_value=(ORGANIZATION_ID,)))
    claims = []

    async def claim(run_id, worker):
        assert loop._capacity.value == 0
        claims.append(run_id)
        return owned if run_id == run.id else None

    async def execute(context, catalog, slot, register):
        assert loop._capacity.value == 0
        await register(control)
        started.set()
        await handed_off.wait()
        slot.release()

    scheduler.claim = AsyncMock(side_effect=claim)
    runner.run = AsyncMock(side_effect=execute)
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await started.wait()
            if begin_without_waiting:
                loop.begin_drain()
                loop.begin_drain()
            else:
                await loop.drain()
            await loop.wait_stopped()
    assert claims == ["loser", run.id]
    assert loop._capacity.value == 1
    assert runner.run.await_count == 1
    control.request_handoff.assert_awaited_once()


async def test_drain_bounds_unfinished_scan_without_claiming(monkeypatch):
    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("candidate",))
    scheduler.claim = AsyncMock()
    entered = Event()

    async def scan(*args, **kwargs):
        entered.set()
        await Event().wait()

    scheduler.scan = AsyncMock(side_effect=scan)
    runner = Mock()
    runner.run = AsyncMock()
    loop = WorkerExecutionLoop(
        Mock(),
        scheduler,
        HarnessPluginFactoryCatalog(()),
        runner,
        build_id="test",
        queue_name="default",
        drain_seconds=0.05,
    )
    monkeypatch.setattr(loop, "_organizations", AsyncMock(return_value=(ORGANIZATION_ID,)))
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await entered.wait()
            await loop.drain()
            await loop.wait_stopped()
    scheduler.claim.assert_not_awaited()
    runner.run.assert_not_awaited()


async def test_restarted_worker_gets_new_identity_and_cannot_inherit_authority(
    interaction_sessions, interaction_object_store
):
    from dataclasses import replace

    from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService

    scheduler = AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    first = WorkerExecutionLoop(
        interaction_sessions, scheduler, Mock(), Mock(), build_id="same-build", queue_name="default"
    )
    restarted = WorkerExecutionLoop(
        interaction_sessions, scheduler, Mock(), Mock(), build_id="same-build", queue_name="default"
    )
    assert first._worker_id != restarted._worker_id
    _, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    claim = await scheduler.claim(run.id, acceptance._worker(worker_id=first._worker_id))
    assert isinstance(claim, ClaimedAttempt)
    context = acceptance._authority(claim)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    await execution.validate(context)
    with pytest.raises(AttemptAuthorityError):
        await execution.validate(replace(context, worker_id=restarted._worker_id))
    assert claim.attempt.worker_id == first._worker_id
