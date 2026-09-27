"""Synchronous request reservations, independent of usage delivery and budget scope."""

from typing import Protocol

from pydantic_ai.exceptions import UsageLimitExceeded


class RequestContribution(Protocol):
    @property
    def call_id(self) -> str | None: ...

    @property
    def record_id(self) -> str: ...


class RequestBudget:
    """Count new generations once, retaining prior attempts as a fixed baseline.

    A continuation reserves no additional request: only its response tells us whether
    it polled the same generation. A new generation is charged before reporting even
    when it exceeds the ceiling. Revisions retain the original dispatch's call ID.
    """

    def __init__(self, *, used: int = 0, limit: int | None = None) -> None:
        self.limit = limit
        self._baseline = used
        self._pending: set[str] = set()
        self._recorded: set[str] = set()

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def used(self) -> int:
        return self._baseline + len(self._recorded) + self.pending

    def reserve(self, call_id: str, *, continuation: bool = False) -> None:
        if self.limit is not None and (
            self.used - self.pending > self.limit or (not continuation and self.used >= self.limit)
        ):
            raise UsageLimitExceeded(f"The request_limit of {self.limit} was exceeded")
        if not continuation:
            self._pending.add(call_id)

    def finish(self, call_id: str, record: RequestContribution) -> UsageLimitExceeded | None:
        """Release and charge atomically; return a refusal to raise after reporting."""
        self.cancel(call_id)
        if record.call_id == call_id:
            self._recorded.add(record.record_id)
        if self.limit is not None and self.used - self.pending > self.limit:
            return UsageLimitExceeded(f"The request_limit of {self.limit} was exceeded")
        return None

    def cancel(self, call_id: str) -> None:
        self._pending.discard(call_id)
