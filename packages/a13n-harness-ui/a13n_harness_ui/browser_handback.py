"""Process-local handback notices for Threads whose browser calls were blocked."""

from collections.abc import Awaitable, Callable

from a13n_logging import get_logger
from anyio import CancelScope
from anyio.abc import TaskGroup
from anyio.lowlevel import checkpoint

BLOCKED = (
    "Human takeover blocks all browser tools. This action was not executed. "
    "A handback notification is registered for this Thread, even if this turn ends. "
    "Browser-runtime failure or Host shutdown cancels this process-local notification. "
    "Continue independent work if possible; otherwise briefly report the blocker and end this turn. "
    "Do not poll or retry browser tools while waiting. After notification, observe afresh before acting."
)
HANDBACK = (
    "Browser human control ended. This Thread previously attempted a browser tool during takeover. "
    "No blocked action was replayed. Obtain a fresh browser_observe result before continuing any "
    "still-relevant browser work. This notice does not resume a previous Goal or its budget."
)


class BrowserHandback:
    """Coalesce by Thread; retry only at handback and root lifecycle boundaries."""

    def __init__(self) -> None:
        self.available = True
        self._episode = 0
        self._pending: dict[str, int] = {}
        self._suppressed: set[str] = set()
        self._scheduled: set[str] = set()
        self._retry: set[str] = set()
        self._deliveries: dict[str, CancelScope] = {}
        self._group: TaskGroup | None = None
        self.deliver: Callable[[str, str, Callable[[], bool]], Awaitable[bool]] | None = None

    def start(self, group: TaskGroup) -> None:
        self._group = group

    def takeover(self) -> None:
        if self.available:
            self._episode += 1
        self.available = False

    def blocked(self, thread_id: str) -> str:
        if thread_id in self._suppressed:
            return "Browser action was not executed. This Thread was stopped; handback notification is suppressed."
        self._pending[thread_id] = self._episode
        self.retry(thread_id)
        return BLOCKED

    def released(self) -> None:
        self.available = True
        for thread_id in self._pending:
            self.retry(thread_id)

    def suppress(self, thread_id: str) -> None:
        self._suppressed.add(thread_id)
        self._pending.pop(thread_id, None)
        if (scope := self._deliveries.get(thread_id)) is not None:
            scope.cancel()

    def user_input(self, thread_id: str) -> None:
        self._suppressed.discard(thread_id)
        self.retry(thread_id)

    def stop(self) -> None:
        self.available = False
        self._pending.clear()
        for scope in self._deliveries.values():
            scope.cancel()

    def retry(self, thread_id: str) -> None:
        if not self.available or self._group is None or thread_id not in self._pending:
            return
        if thread_id in self._scheduled:
            self._retry.add(thread_id)
            return
        self._scheduled.add(thread_id)
        self._group.start_soon(self._deliver, thread_id)

    async def _deliver(self, thread_id: str) -> None:
        scope = CancelScope()
        self._deliveries[thread_id] = scope
        try:
            with scope:
                await checkpoint()
                episode = self._pending.get(thread_id)

                def current() -> bool:
                    return (
                        self.available
                        and episode is not None
                        and self._pending.get(thread_id) == episode
                        and thread_id not in self._suppressed
                    )

                if self.deliver is not None and current():
                    if await self.deliver(thread_id, HANDBACK, current) and self._pending.get(thread_id) == episode:
                        self._pending.pop(thread_id, None)
        except Exception:
            get_logger(__name__).warning("Could not deliver browser handback notice: %s", thread_id, exc_info=True)
        finally:
            self._deliveries.pop(thread_id, None)
            self._scheduled.discard(thread_id)
            if thread_id in self._retry:
                self._retry.discard(thread_id)
                self.retry(thread_id)
