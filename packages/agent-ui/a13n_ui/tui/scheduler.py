"""Bounded render projection coalescing for the Textual boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from a13n_ui.tui.models import ProjectionHints, TerminalState

RenderCallback = Callable[[TerminalState, ProjectionHints], Awaitable[None]]


class ProjectionScheduler:
    """Keep at most one delayed render and preserve every semantic hint."""

    def __init__(
        self,
        render: RenderCallback,
        *,
        interval_seconds: float = 1 / 30,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._render = render
        self._interval = interval_seconds
        self._pending_state: TerminalState | None = None
        self._pending_hints: ProjectionHints | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(
        self,
        state: TerminalState,
        hints: ProjectionHints,
        *,
        immediate: bool = False,
    ) -> None:
        async with self._lock:
            if self._closed:
                return
            self._pending_state = state
            self._pending_hints = _merge_hints(self._pending_hints, hints)
            if immediate:
                task = self._task
                self._task = None
            elif self._task is None or self._task.done():
                self._task = asyncio.create_task(self._delayed_flush())
                return
            else:
                return
        if task is not None:
            task.cancel()
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if self._closed or self._pending_state is None or self._pending_hints is None:
                return
            state = self._pending_state
            hints = self._pending_hints
            self._pending_state = None
            self._pending_hints = None
        await self._render(state, hints)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            task = self._task
            self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.flush()
        async with self._lock:
            self._closed = True

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(self._interval)
            await self.flush()
        except asyncio.CancelledError:
            pass


def _merge_hints(previous: ProjectionHints | None, current: ProjectionHints) -> ProjectionHints:
    if previous is None:
        return current
    return replace(
        current,
        changed=previous.changed | current.changed,
        scroll_to_latest=previous.scroll_to_latest or current.scroll_to_latest,
        preserve_anchor=current.preserve_anchor or previous.preserve_anchor,
    )


__all__ = ["ProjectionScheduler", "RenderCallback"]
