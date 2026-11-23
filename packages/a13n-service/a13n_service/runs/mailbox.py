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


_STEER_KINDS = ("message", "child_message")
REALTIME_NOTICE_KINDS = frozenset({"child_result", "child_message", "tool_result"})


class Mailbox:
    def __init__(self, storage: Storage, *, interval: float):
        self.storage = storage
        self.interval = interval
        self._listeners: dict[str, set[asyncio.Event]] = {}
        self._realtime_listeners: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None

    def notify(self, session: AsyncSession, entry: InboxEntryRow) -> None:
        """Wake local receivers only after durable input commits; remote receivers scan the same inbox."""
        if entry.delivery != "steer":
            return
        if entry.kind in _STEER_KINDS:
            after_commit(session, partial(self._wake, entry.thread_id))
        if entry.kind in REALTIME_NOTICE_KINDS:
            after_commit(session, partial(self._wake, entry.thread_id, realtime=True))

    async def _wake(self, thread_id: str, *, realtime: bool = False) -> None:
        listeners = self._realtime_listeners if realtime else self._listeners
        for ready in listeners.get(thread_id, ()):
            ready.set()

    @asynccontextmanager
    async def listen(self, thread_id: str, *, realtime: bool = False) -> AsyncIterator[asyncio.Event]:
        ready = asyncio.Event()
        group = self._realtime_listeners if realtime else self._listeners
        group.setdefault(thread_id, set()).add(ready)
        if self._task is None:
            self._task = asyncio.create_task(self._scan(), name="inbox-availability")
        try:
            yield ready
        finally:
            listeners = group[thread_id]
            listeners.remove(ready)
            if not listeners:
                del group[thread_id]
            if not self._listeners and not self._realtime_listeners:
                task, self._task = self._task, None
                assert task is not None
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _scan(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self.interval):
                    if self._listeners:
                        for thread_id in await self._pending(tuple(self._listeners)):
                            await self._wake(thread_id)
                    if self._realtime_listeners:
                        for thread_id in await self._pending(tuple(self._realtime_listeners), realtime=True):
                            await self._wake(thread_id, realtime=True)
            except Exception as error:
                # No input was assigned or consumed. A later scan or checkpoint retries it.
                logger.warning("Inbox availability scan failed", extra={"error_type": type(error).__name__})
            await asyncio.sleep(self.interval)

    async def _pending(self, thread_ids: tuple[str, ...], *, realtime: bool = False) -> set[str]:
        kinds = REALTIME_NOTICE_KINDS if realtime else _STEER_KINDS
        async with short_session(self.storage) as session:
            return set(
                await session.scalars(
                    select(InboxEntryRow.thread_id)
                    .where(
                        InboxEntryRow.thread_id.in_(thread_ids),
                        InboxEntryRow.status == "pending",
                        InboxEntryRow.kind.in_(kinds),
                        InboxEntryRow.delivery == "steer",
                    )
                    .distinct()
                )
            )
