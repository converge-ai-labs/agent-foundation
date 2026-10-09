"""Stopping a caller is independent of draining its children before shared resources close."""

import asyncio

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
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


async def test_application_shutdown_owns_services_and_children_before_clients_close(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service import app as app_module
    from a13n_service.settings import Settings

    tasks = Tasks()
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order = []

    async def child() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            order.append("child")

    async def clients_close() -> None:
        order.append("clients")

    async def open_runtime(stack, *args, **kwargs):  # type: ignore[no-untyped-def]
        stack.push_async_callback(clients_close)
        stack.push_async_callback(tasks.close, timeout=1)
        return SimpleNamespace(storage=object(), tasks=tasks, endpoint_policy=EndpointPolicy())

    class Worker:
        def __init__(self, *args):  # type: ignore[no-untyped-def]
            pass

        async def run(self) -> None:
            task = tasks.start(child(), name="preparation")
            try:
                await asyncio.Event().wait()
            finally:
                task.cancel()  # Ordinary caller only signals its child; the process owns the rest.
                order.append("worker")

    monkeypatch.setattr(app_module, "open_runtime", open_runtime)
    monkeypatch.setattr(app_module, "check_schema", AsyncMock())
    monkeypatch.setattr(app_module, "Worker", Worker)
    app = app_module.build_app(role="worker", settings=Settings())

    async def application() -> None:
        async with app.router.lifespan_context(app):
            await started.wait()

    closing = asyncio.create_task(application())
    await cleaning.wait()
    assert order == ["worker"]
    release.set()
    await closing
    assert order == ["worker", "child", "clients"]


async def test_application_shutdown_does_not_wait_forever_for_a_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service import app as app_module
    from a13n_service.settings import Settings

    tasks = Tasks()
    started, release = asyncio.Event(), asyncio.Event()

    async def open_runtime(stack, *args, **kwargs):  # type: ignore[no-untyped-def]
        stack.push_async_callback(tasks.close, timeout=0.02)
        return SimpleNamespace(storage=object(), tasks=tasks, endpoint_policy=EndpointPolicy())

    class Worker:
        def __init__(self, *args):  # type: ignore[no-untyped-def]
            pass

        async def run(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()

    monkeypatch.setattr(app_module, "open_runtime", open_runtime)
    monkeypatch.setattr(app_module, "check_schema", AsyncMock())
    monkeypatch.setattr(app_module, "Worker", Worker)
    app = app_module.build_app(role="worker", settings=Settings())
    try:
        async with asyncio.timeout(1):
            async with app.router.lifespan_context(app):
                await started.wait()
        assert len(tasks.pending) == 1
    finally:
        release.set()
        await asyncio.gather(*tasks.pending, return_exceptions=True)
