"""Independent I/O stays bounded and settles before its caller continues."""

import asyncio

import pytest
from a13n_service.infra.concurrency import map_concurrently

pytestmark = pytest.mark.anyio


async def test_bounded_work_preserves_order_and_waits_for_peers_after_failure():
    admitted = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum = 0
    completed = []

    async def run(item):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 3:
            admitted.set()
        try:
            await release.wait()
            if item == 0:
                raise ValueError("failed item")
            completed.append(item)
            return item
        finally:
            active -= 1

    task = asyncio.create_task(map_concurrently(list(range(9)), run, limit=3))
    await asyncio.wait_for(admitted.wait(), 1)
    assert not task.done()
    release.set()
    with pytest.raises(ValueError, match="failed item"):
        await task
    assert maximum == 3
    assert active == 0
    assert sorted(completed) == [1, 2]


async def test_parent_cancellation_drains_started_work():
    started = asyncio.Event()
    drained = []

    async def run(item):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            drained.append(item)

    task = asyncio.create_task(map_concurrently(list(range(5)), run, limit=2))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(drained) == [0, 1]
