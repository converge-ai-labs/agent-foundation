"""Process-owned tasks that may outlive the operation that asked them to stop."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from a13n_logging import exception_details, get_logger

logger = get_logger(__name__)


class Tasks:
    """Keep children alive through cancellation cleanup, before shared clients close at process exit."""

    def __init__(self) -> None:
        self.pending: set[asyncio.Task[Any]] = set()
        self.closed = False

    def start[T](self, work: Coroutine[Any, Any, T], *, name: str) -> asyncio.Task[T]:
        if self.closed:
            work.close()
            raise RuntimeError("Process tasks are closed")
        task = asyncio.create_task(work, name=name)
        self.pending.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self.pending.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(
                "Process task failed",
                extra={"task": task.get_name(), "exception_details": exception_details(error)},
            )

    async def close(self, *, timeout: float) -> None:
        if self.closed:
            return
        self.closed = True
        for task in self.pending:
            # A second cancellation would interrupt the child's own bounded cleanup.
            if not task.cancelling():
                task.cancel()
        if self.pending:
            _, unfinished = await asyncio.wait(self.pending, timeout=timeout)
            if unfinished:
                logger.warning("Process task cleanup exceeded shutdown deadline", extra={"count": len(unfinished)})
