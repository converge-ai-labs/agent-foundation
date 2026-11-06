"""One App grace deadline, separate from ownership-preserving cleanup."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from a13n_logging import get_logger
from anyio import CancelScope, move_on_after


class Shutdown:
    def __init__(self) -> None:
        self.started: float | None = None
        self.deadline = float("inf")
        self.phase = "running"
        self._scopes: set[CancelScope] = set()
        self._expedited = False

    def begin(self, timeout_seconds: float) -> None:
        if self.started is None:
            self.started = monotonic()
            self.deadline = self.started + (0 if self._expedited else timeout_seconds)

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - monotonic())

    def expedite(self) -> None:
        """End grace without cancelling finalization or abandoning owned tasks."""
        self._expedited = True
        self.deadline = monotonic()
        for scope in tuple(self._scopes):
            scope.cancel()

    @contextmanager
    def grace(self) -> Iterator[CancelScope]:
        with move_on_after(self.remaining, shield=True) as scope:
            self._scopes.add(scope)
            try:
                yield scope
            finally:
                self._scopes.discard(scope)

    def enter(self, phase: str) -> None:
        self.phase = phase
        get_logger(__name__).info("Stopping App: %s", self.describe())

    def describe(self) -> str:
        elapsed = 0.0 if self.started is None else monotonic() - self.started
        return f"{self.phase}; elapsed={elapsed:.1f}s, grace_remaining={self.remaining:.1f}s"
