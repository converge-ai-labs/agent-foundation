from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import SafeFailure
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.worker import WorkerExecutionLoop
from a13n_service.plugins.commands import PluginRuntimeCommandFailure
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target, installed_harness_version
from anyio import Event, create_task_group, fail_after

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID

pytestmark = pytest.mark.anyio


def _lock():
    lock = PluginRuntimeLock(
        mode="on_demand",
        runtime_target=default_runtime_target(),
        worker_release="test",
        harness_version=installed_harness_version(),
        digest="0" * 64,
    )
    return lock.model_copy(update={"digest": lock.computed_digest()})


async def test_worker_reserves_before_claim_releases_losers_and_drains_owned_roots(
    interaction_sessions,
    interaction_object_store,
    monkeypatch,
):
    _, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    owned = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, acceptance._worker()
    )
    assert isinstance(owned, ClaimedAttempt)
    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("loser", run.id))
    runtime = Mock()
    runtime.prepare = AsyncMock(return_value=HarnessPluginFactoryCatalog(()))
    runner = Mock()
    started, handed_off = Event(), Event()
    control = Mock(spec=RunAttemptControl)
    control.request_handoff = AsyncMock(side_effect=lambda reason: handed_off.set())
    loop = WorkerExecutionLoop(
        interaction_sessions,
        scheduler,
        runtime,
        runner,
        build_id="test",
        queue_name="default",
        concurrency=1,
        poll_seconds=0.01,
        drain_seconds=0.5,
    )
    monkeypatch.setattr(loop, "_candidates", AsyncMock(return_value=((ORGANIZATION_ID, _lock()),)))
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
            await loop.drain()
            await loop.wait_stopped()
    assert claims == ["loser", run.id]
    assert loop._capacity.value == 1
    assert runner.run.await_count == 1
    control.request_handoff.assert_awaited_once()


async def test_drain_bounds_unfinished_preflight_without_claiming(monkeypatch):
    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("candidate",))
    scheduler.claim = AsyncMock()
    entered = Event()

    async def prepare(lock):
        entered.set()
        await Event().wait()

    runtime = Mock()
    runtime.prepare = AsyncMock(side_effect=prepare)
    runner = Mock()
    runner.run = AsyncMock()
    loop = WorkerExecutionLoop(
        Mock(),
        scheduler,
        runtime,
        runner,
        build_id="test",
        queue_name="default",
        drain_seconds=0.05,
    )
    monkeypatch.setattr(loop, "_candidates", AsyncMock(return_value=((ORGANIZATION_ID, _lock()),)))
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await entered.wait()
            await loop.drain()
            await loop.wait_stopped()
    scheduler.claim.assert_not_awaited()
    runner.run.assert_not_awaited()


@pytest.mark.parametrize("reserved", [False, True])
async def test_retirement_counts_pending_claims_as_busy(reserved):
    loop = WorkerExecutionLoop(Mock(), Mock(), Mock(), Mock(), build_id="test", queue_name="default", concurrency=1)
    if reserved:
        loop._capacity.acquire_nowait()
    assert await loop.retire_if_idle() is not reserved
    assert loop.is_draining() is not reserved


async def test_runtime_failure_leaves_work_unclaimed_and_discovery_running(monkeypatch):
    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("candidate",))
    scheduler.claim = AsyncMock()
    runtime = Mock()
    retried = Event()
    loop = WorkerExecutionLoop(
        Mock(), scheduler, runtime, Mock(), build_id="test", queue_name="default", poll_seconds=0.01
    )

    async def prepare(lock):
        if runtime.prepare.await_count > 1:
            retried.set()
        raise PluginRuntimeCommandFailure(SafeFailure(code="plugin_runtime_capacity_exceeded", message="Full"))

    runtime.prepare = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(loop, "_candidates", AsyncMock(return_value=((ORGANIZATION_ID, _lock()),)))
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await retried.wait()
            await loop.drain()
            await loop.wait_stopped()
    scheduler.claim.assert_not_awaited()
