"""One lightweight inbox scan for this worker's immediate-steering listeners.

The mailbox only hints that durable input may be available. The ordinary inbox
assignment transaction still owns eligibility, authority, ordering and consumption.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, after_commit, short_session
from a13n_service.runs.tables import InboxEntryRow

logger = get_logger(__name__)


class Mailbox:
    def __init__(self, storage: Storage, *, interval: float):
        self.storage = storage
        self.interval = interval
        self._listeners: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None

    def notify(self, session: AsyncSession, entry: InboxEntryRow) -> None:
        """Wake local receivers only after durable input commits; remote receivers scan the same inbox."""
        if entry.kind == "message" and entry.delivery == "steer":
            after_commit(session, partial(self._wake, entry.thread_id))

    async def _wake(self, thread_id: str) -> None:
        for ready in self._listeners.get(thread_id, ()):
            ready.set()

    @asynccontextmanager
    async def listen(self, thread_id: str) -> AsyncIterator[asyncio.Event]:
        ready = asyncio.Event()
        self._listeners.setdefault(thread_id, set()).add(ready)
        if self._task is None:
            self._task = asyncio.create_task(self._scan(), name="inbox-availability")
        try:
            yield ready
        finally:
            listeners = self._listeners[thread_id]
            listeners.remove(ready)
            if not listeners:
                del self._listeners[thread_id]
            if not self._listeners:
                task, self._task = self._task, None
                assert task is not None
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _scan(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self.interval):
                    pending = await self._pending(tuple(self._listeners))
                for thread_id in pending:
                    await self._wake(thread_id)
            except Exception as error:
                # No input was assigned or consumed. A later scan or checkpoint retries it.
                logger.warning("Inbox availability scan failed", extra={"error_type": type(error).__name__})
            await asyncio.sleep(self.interval)

    async def _pending(self, thread_ids: tuple[str, ...]) -> set[str]:
        async with short_session(self.storage) as session:
            return set(
                await session.scalars(
                    select(InboxEntryRow.thread_id)
                    .where(
                        InboxEntryRow.thread_id.in_(thread_ids),
                        InboxEntryRow.status == "pending",
                        InboxEntryRow.kind == "message",
                        InboxEntryRow.delivery == "steer",
                    )
                    .distinct()
                )
            )
