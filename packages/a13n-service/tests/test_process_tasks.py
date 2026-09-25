"""Stopping a caller is independent of draining its children before shared resources close."""

import asyncio

import pytest
from a13n_service.infra.tasks import Tasks

pytestmark = pytest.mark.anyio


async def test_an_already_cancelled_child_finishes_cleanup_without_a_second_cancel() -> None:
    tasks = Tasks()
    started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    completed = []

    async def child() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()
            completed.append("cleanup")

    task = tasks.start(child(), name="child")
    await started.wait()
    task.cancel()
    await cleaning.wait()
    closing = asyncio.create_task(tasks.close(timeout=5))
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    await closing
    assert completed == ["cleanup"] and task.cancelled()


async def test_shutdown_is_bounded_even_when_a_child_ignores_cancellation() -> None:
    tasks = Tasks()
    started, release = asyncio.Event(), asyncio.Event()

    async def child() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()

    task = tasks.start(child(), name="stuck-child")
    try:
        await started.wait()
        async with asyncio.timeout(1):
            await tasks.close(timeout=0.01)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
