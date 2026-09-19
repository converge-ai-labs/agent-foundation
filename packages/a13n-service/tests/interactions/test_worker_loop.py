from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService
from a13n_service.interactions.lease_renewals import LeaseRenewalBatcher
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
        # Successful drain includes a real database renewal; allow CI scheduling
        # headroom. The unfinished-scan test covers the hard deadline separately.
        drain_seconds=5,
    )
    monkeypatch.setattr(loop, "_organizations", AsyncMock(return_value=(ORGANIZATION_ID,)))
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    renewals = LeaseRenewalBatcher(execution)
    loop._renewals = renewals
    renewed_during_drain = []
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
        renewed_during_drain.append(await renewals.renew(acceptance._authority(owned)))
        slot.release()

    scheduler.claim = AsyncMock(side_effect=claim)
    runner.run = AsyncMock(side_effect=execute)
    with fail_after(10):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await started.wait()
            if begin_without_waiting:
                loop.begin_drain()
                loop.begin_drain()
            else:
                await loop.drain()
            await loop.wait_stopped()
    assert len(renewed_during_drain) == 1
    with pytest.raises(AttemptAuthorityError):
        await renewals.renew(acceptance._authority(owned))
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


async def test_productive_worker_rescans_without_waiting_for_poll_interval(
    interaction_sessions, interaction_object_store, monkeypatch
):
    _, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    owned = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, acceptance._worker()
    )
    assert isinstance(owned, ClaimedAttempt)
    scheduler = Mock(spec=AttemptScheduler)
    runner = Mock()
    started, rescanned, handed_off = Event(), Event(), Event()
    control = Mock(spec=RunAttemptControl)
    control.request_handoff = AsyncMock(side_effect=lambda reason: handed_off.set())
    loop = WorkerExecutionLoop(
        interaction_sessions,
        scheduler,
        HarnessPluginFactoryCatalog(()),
        runner,
        build_id="test",
        queue_name="default",
        concurrency=2,
        poll_seconds=60,
    )
    monkeypatch.setattr(loop, "_organizations", AsyncMock(return_value=(ORGANIZATION_ID,)))

    async def scan(*args, **kwargs):
        if scheduler.claim.await_count == 0:
            return (run.id,)
        rescanned.set()
        return ()

    async def execute(context, catalog, slot, register):
        await register(control)
        started.set()
        await handed_off.wait()

    scheduler.scan = AsyncMock(side_effect=scan)
    scheduler.claim = AsyncMock(return_value=owned)
    runner.run = AsyncMock(side_effect=execute)
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(loop.run)
            await started.wait()
            await rescanned.wait()
            await loop.drain()
            await loop.wait_stopped()
    assert loop._capacity.value == 2
    scheduler.claim.assert_awaited_once()


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


@pytest.mark.parametrize("boundary", ["organizations", "scan", "claim"])
@pytest.mark.parametrize("sqlstate", [None, "40001", "40P01", "55P03", "57014"])
async def test_database_disconnect_retries_admission_without_losing_capacity(monkeypatch, caplog, boundary, sqlstate):
    import psycopg
    from anyio import current_time
    from sqlalchemy.exc import OperationalError

    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("candidate",))
    scheduler.claim = AsyncMock(return_value=None)
    loop = WorkerExecutionLoop(
        Mock(),
        scheduler,
        HarnessPluginFactoryCatalog(()),
        Mock(),
        build_id="test",
        queue_name="default",
        concurrency=1,
        poll_seconds=0.02,
    )
    organizations = AsyncMock(return_value=(ORGANIZATION_ID,))
    monkeypatch.setattr(loop, "_organizations", organizations)
    operation = {"organizations": organizations, "scan": scheduler.scan, "claim": scheduler.claim}[boundary]
    calls = []

    async def disconnected_then_recovered(*args, **kwargs):
        calls.append(current_time())
        if len(calls) == 1:
            cause = (
                psycopg.errors.lookup(sqlstate)("test contention")
                if sqlstate
                else psycopg.OperationalError("connection refused")
            )
            raise OperationalError(None, None, cause)
        loop.begin_drain()
        return None if boundary == "claim" else ()

    operation.side_effect = disconnected_then_recovered
    with fail_after(2):
        await loop.run()
    assert len(calls) == 2 and calls[1] - calls[0] >= 0.02
    assert loop._capacity.value == 1
    assert loop._admission_scope is None
    assert ("worker_database_contention" if sqlstate else "worker_database_unavailable") in caplog.text


async def test_worker_does_not_retry_programming_failure(monkeypatch):
    loop = WorkerExecutionLoop(
        Mock(), Mock(), HarnessPluginFactoryCatalog(()), Mock(), build_id="test", queue_name="default"
    )
    organizations = AsyncMock(side_effect=RuntimeError("invalid scheduler"))
    monkeypatch.setattr(loop, "_organizations", organizations)
    with pytest.raises(ExceptionGroup, match="TaskGroup") as caught:
        await loop.run()
    assert isinstance(caught.value.exceptions[0], RuntimeError)
    organizations.assert_awaited_once()


async def test_database_disconnect_after_productive_claim_still_backs_off(monkeypatch):
    import psycopg
    from a13n_service.interactions.scheduling import SealedClaim
    from anyio import current_time
    from sqlalchemy.exc import OperationalError

    scheduler = Mock(spec=AttemptScheduler)
    scheduler.scan = AsyncMock(return_value=("sealed", "pending"))
    loop = WorkerExecutionLoop(
        Mock(), scheduler, Mock(), Mock(), build_id="test", queue_name="default", concurrency=1, poll_seconds=0.02
    )
    monkeypatch.setattr(loop, "_organizations", AsyncMock(return_value=(ORGANIZATION_ID,)))
    calls = []

    async def claim(*args):
        calls.append(current_time())
        if len(calls) == 1:
            return Mock(spec=SealedClaim)
        if len(calls) == 2:
            raise OperationalError(None, None, psycopg.OperationalError("connection refused"))
        loop.begin_drain()
        return None

    scheduler.claim = AsyncMock(side_effect=claim)
    with fail_after(2):
        await loop.run()
    assert len(calls) == 3 and calls[2] - calls[1] >= 0.02
    assert loop._capacity.value == 1
