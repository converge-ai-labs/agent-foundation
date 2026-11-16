"""Browser registrations produce inbox events when human control ends."""

from collections.abc import Awaitable, Callable
from uuid import uuid4

from a13n_logging import get_logger
from anyio.abc import TaskGroup

BLOCKED = (
    "Human takeover blocks all browser tools. This action was not executed. "
    "A handback notification is registered for this Thread, even if this turn ends. "
    "Browser-runtime failure or Host shutdown cancels this process-local registration. "
    "Continue independent work if possible; otherwise briefly report the blocker and end this turn. "
    "Do not poll or retry browser tools while waiting. After notification, observe afresh before acting."
)
HANDBACK = (
    "Browser human control ended. This Thread requested a handback reminder. "
    "No blocked action was replayed. Obtain a fresh browser_observe result before continuing any "
    "still-relevant browser work. This notice does not resume a previous Goal or its budget."
)


class BrowserHandback:
    """Own registration and cancellation, not Thread execution or delivery retry."""

    def __init__(self) -> None:
        self.available = True
        self._episode = 0
        self._registrations: dict[str, tuple[int, str]] = {}
        self._pending: set[str] = set()
        self._suppressed: set[str] = set()
        self._group: TaskGroup | None = None
        self.deliver: Callable[[str, str, Callable[[], bool], str], Awaitable[bool]] | None = None
        self.withdraw: Callable[[str, str], Awaitable[None]] | None = None

    def start(self, group: TaskGroup) -> None:
        self._group = group

    def takeover(self) -> None:
        if not self.available:
            return
        self._episode += 1
        self.available = False
        for thread_id, (_, identity) in tuple(self._registrations.items()):
            self._withdraw(thread_id, identity)
            self._registrations[thread_id] = (self._episode, f"handback-{uuid4().hex}")
            self._pending.add(thread_id)

    def register(self, thread_id: str) -> dict[str, str]:
        if thread_id in self._suppressed:
            return {"status": "suppressed", "message": "This Thread was stopped; handback notification is suppressed."}
        if thread_id not in self._registrations:
            self._registrations[thread_id] = (self._episode, f"handback-{uuid4().hex}")
            self._pending.add(thread_id)
        self.retry(thread_id)
        return {"status": "registered", "message": "One handback reminder is registered for this Thread."}

    def blocked(self, thread_id: str) -> str:
        if thread_id in self._suppressed:
            return "Browser action was not executed. This Thread was stopped; handback notification is suppressed."
        self.register(thread_id)
        return BLOCKED

    def released(self) -> None:
        self.available = True
        for thread_id in tuple(self._pending):
            self.retry(thread_id)

    def _withdraw(self, thread_id: str, identity: str) -> None:
        if self._group is not None and self.withdraw is not None:
            self._group.start_soon(self._withdraw_retained, thread_id, identity)

    async def _withdraw_retained(self, thread_id: str, identity: str) -> None:
        if self.withdraw is not None:
            await self.withdraw(thread_id, identity)

    def suppress(self, thread_id: str) -> None:
        self._suppressed.add(thread_id)
        self._pending.discard(thread_id)
        if registration := self._registrations.pop(thread_id, None):
            self._withdraw(thread_id, registration[1])

    def user_input(self, thread_id: str) -> None:
        self._suppressed.discard(thread_id)
        self.retry(thread_id)

    def stop(self) -> None:
        self.available = False
        self._pending.clear()
        self._registrations.clear()

    def retry(self, thread_id: str) -> None:
        if self.available and self._group is not None and thread_id in self._pending:
            self._group.start_soon(self._publish, thread_id)

    async def _publish(self, thread_id: str) -> None:
        if thread_id not in self._pending:
            return
        self._pending.remove(thread_id)
        registration = self._registrations[thread_id]

        def current() -> bool:
            return self.available and self._registrations.get(thread_id) == registration

        try:
            accepted = (
                self.deliver is not None
                and current()
                and await self.deliver(thread_id, HANDBACK, current, registration[1])
            )
            if accepted:
                if not current():
                    self._withdraw(thread_id, registration[1])
            elif self._registrations.get(thread_id) == registration:
                self._pending.add(thread_id)
        except Exception:
            if self._registrations.get(thread_id) == registration:
                self._pending.add(thread_id)
            get_logger(__name__).warning("Could not retain browser handback event: %s", thread_id, exc_info=True)
