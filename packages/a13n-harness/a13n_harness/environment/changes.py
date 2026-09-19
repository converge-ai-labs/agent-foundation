"""Internal run-local Environment change journal."""

from __future__ import annotations

import asyncio

from a13n_harness.providers.environment.models import EnvironmentChange, EnvironmentError


class EnvironmentChangeJournal:
    """Append-only run-local journal with independent sequence cursors."""

    def __init__(self) -> None:
        self._changes: list[EnvironmentChange] = []
        self._changed = asyncio.Event()
        self._closed = False

    @property
    def current_sequence(self) -> int:
        return len(self._changes)

    def publish(self, change: EnvironmentChange) -> None:
        """Append one committed change without introducing an await point."""
        if self._closed:
            raise EnvironmentError("The Environment change journal is closed.", code="environment_closed")
        if change.sequence != len(self._changes) + 1:
            raise EnvironmentError(
                "Environment change sequence is not contiguous.",
                code="environment_change_invalid",
            )
        self._changes.append(change)
        changed = self._changed
        self._changed = asyncio.Event()
        changed.set()

    async def read(self, *, after_sequence: int, wait: bool = False) -> tuple[EnvironmentChange, ...]:
        while True:
            if (
                not isinstance(after_sequence, int)
                or isinstance(after_sequence, bool)
                or after_sequence < 0
                or after_sequence > len(self._changes)
            ):
                raise EnvironmentError(
                    "Environment change cursor is invalid.",
                    code="environment_change_cursor_invalid",
                    details={
                        "after_sequence": after_sequence,
                        "current_sequence": len(self._changes),
                    },
                )
            matching = tuple(change for change in self._changes if change.sequence > after_sequence)
            if matching:
                return matching
            if self._closed:
                raise EnvironmentError("The Environment change journal is closed.", code="environment_closed")
            if not wait:
                return ()
            changed = self._changed
            await changed.wait()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._changed.set()
