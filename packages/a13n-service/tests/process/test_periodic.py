from __future__ import annotations

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
