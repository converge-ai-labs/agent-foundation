from __future__ import annotations

from functools import partial

import anyio
import pytest
from a13n_service.background import PeriodicTask, Sweep

pytestmark = pytest.mark.anyio


async def test_transient_failure_retries_and_cancellation_stops_scanning() -> None:
    calls = 0
    completed = anyio.Event()

    async def scan() -> Sweep:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("dependency unavailable")
        completed.set()
        return Sweep(examined=2, completed=1, deferred=1)

    task = PeriodicTask("test", scan, interval_seconds=0.001, timeout_seconds=1)
    with anyio.fail_after(1):
        async with anyio.create_task_group() as group:
            group.start_soon(task.run)
            await completed.wait()
            group.cancel_scope.cancel()
    assert calls == 2
    assert task.last_result == Sweep(examined=2, completed=1, deferred=1)
    assert task.last_outcome == "deferred"


async def test_hung_scan_is_cancelled_before_retry_without_overlap() -> None:
    active = 0
    calls = 0
    retried = anyio.Event()

    async def scan() -> Sweep:
        nonlocal active, calls
        assert active == 0
        active += 1
        calls += 1
        try:
            if calls == 1:
                await anyio.sleep_forever()
            retried.set()
            return Sweep()
        finally:
            active -= 1

    task = PeriodicTask("test", scan, interval_seconds=0.001, timeout_seconds=0.01)
    with anyio.fail_after(1):
        async with anyio.create_task_group() as group:
            group.start_soon(task.run)
            await retried.wait()
            group.cancel_scope.cancel()
    assert calls == 2
    assert active == 0


async def test_programming_failure_reaches_process_supervision() -> None:
    async def scan() -> Sweep:
        raise RuntimeError("broken component")

    task = PeriodicTask("test", scan, interval_seconds=1, timeout_seconds=1)
    with pytest.raises(RuntimeError, match="broken component"):
        await task.run()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
async def test_timing_bounds_must_be_finite_and_positive(value: float) -> None:
    async def scan() -> Sweep:
        return Sweep()

    with pytest.raises(ValueError):
        PeriodicTask("test", scan, interval_seconds=value, timeout_seconds=1)
    with pytest.raises(ValueError):
        PeriodicTask("test", scan, interval_seconds=1, timeout_seconds=value)


async def test_drain_before_start_skips_the_first_scan() -> None:
    async def scan() -> Sweep:
        pytest.fail("drained loop started a scan")

    task = PeriodicTask("test", scan, interval_seconds=60, timeout_seconds=1)
    task.drain()
    await task.run()
    with anyio.fail_after(1):
        await task.wait_stopped()


async def test_shutdown_wakes_an_idle_loop_without_waiting_for_the_interval() -> None:
    scanned = anyio.Event()
    calls = 0

    async def scan() -> Sweep:
        nonlocal calls
        calls += 1
        scanned.set()
        return Sweep()

    task = PeriodicTask("test", scan, interval_seconds=60, timeout_seconds=1)
    with anyio.fail_after(1):
        async with anyio.create_task_group() as group:
            group.start_soon(task.run)
            await scanned.wait()
            await anyio.wait_all_tasks_blocked()
            await task.shutdown(timeout_seconds=1)
    assert calls == 1


async def test_shutdown_finishes_an_active_scan_without_starting_another() -> None:
    entered = anyio.Event()
    finish = anyio.Event()
    calls = 0

    async def scan() -> Sweep:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return Sweep(completed=1)

    task = PeriodicTask("test", scan, interval_seconds=0.001, timeout_seconds=1)
    with anyio.fail_after(1):
        async with anyio.create_task_group() as group:
            group.start_soon(task.run)
            await entered.wait()
            group.start_soon(partial(task.shutdown, timeout_seconds=1))
            await anyio.wait_all_tasks_blocked()
            assert task.is_draining()
            assert task.last_result is None
            finish.set()
            await task.wait_stopped()
    assert calls == 1
    assert task.last_result == Sweep(completed=1)


async def test_drain_deadline_leaves_unfinished_scan_for_process_cancellation() -> None:
    entered = anyio.Event()
    cancelled = anyio.Event()

    async def scan() -> Sweep:
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    task = PeriodicTask("test", scan, interval_seconds=60, timeout_seconds=60)
    with anyio.fail_after(1):
        async with anyio.create_task_group() as group:
            group.start_soon(task.run)
            await entered.wait()
            await task.shutdown(timeout_seconds=0.01)
            assert task.is_draining()
            assert not cancelled.is_set()
            group.cancel_scope.cancel()
        await task.wait_stopped()
    assert cancelled.is_set()
    assert task.last_result is None
    assert task.last_outcome is None
