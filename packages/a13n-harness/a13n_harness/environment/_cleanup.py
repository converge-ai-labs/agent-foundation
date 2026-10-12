"""Cancellation-safe cleanup workers shared by activation and aggregate teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_SUPERVISED_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def _supervise_cleanup_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.add(task)
    task.add_done_callback(_consume_background_task)


async def _await_cleanup_shielded(operation: Coroutine[Any, Any, None]) -> None:
    """Finish one owned teardown worker before propagating any caller cancellation."""
    cleanup = asyncio.create_task(operation)
    cancellations: list[asyncio.CancelledError] = []
    current = asyncio.current_task()
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancellations.append(exc)
            if current is not None:
                current.uncancel()
    cleanup_error: BaseException | None = None
    try:
        cleanup.result()
    except BaseException as exc:
        cleanup_error = exc
    if cancellations:
        if cleanup_error is not None:
            raise BaseExceptionGroup(
                "Environment cancellation and cleanup failed",
                [cancellations[0], cleanup_error],
            ) from None
        raise cancellations[0]
    if cleanup_error is not None:
        raise cleanup_error
