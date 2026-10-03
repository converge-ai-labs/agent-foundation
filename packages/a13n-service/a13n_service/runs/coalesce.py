"""Coalescing an attempt's output: consecutive fragments of one stream become one stream event.

Model deltas carry a token or a few, so streaming each as its own event would append thousands of entries per step.
The coalescer holds one fragment event (text, reasoning or tool-call arguments, see `display.fragment`) and extends it
with the fragments that continue it until another event arrives, the window elapses, a boundary needs the display, or
the attempt ends. Only then does the event get its sequence, so sequences stay dense, and folding the merged event
leaves the display's items as folding its parts would, apart from the stream positions they record.
"""

import asyncio
from typing import Any

from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol.display import DisplayFold, Observed, extend, fragment

from a13n_service.runs.stream import ThreadStream

# The size in which the observer splits long input text: merging never rebuilds what it split, and a merged event
# stays far below the stream's per-delta limit.
MAX_MERGED_CHARS = 8192


class Coalescer:
    """The path from an attempt's Harness events to its display fold and thread stream. It holds at most one
    fragment event, for at most `window` seconds from the fragment that started it; a window of 0 holds none."""

    def __init__(self, fold: DisplayFold, stream: ThreadStream, *, window: float) -> None:
        self.fold, self.stream, self.window = fold, stream, window
        self.held: dict[str, Any] | None = None
        # The stream the held fragment continues, and the length of its text so far.
        self.held_stream: object = None
        self.held_length = 0
        self.timer: asyncio.TimerHandle | None = None

    async def __aenter__(self) -> "Coalescer":
        return self

    async def __aexit__(self, *_: object) -> None:
        # The attempt ended, failed or was cancelled: what it observed still reaches the display and the stream.
        self.flush()

    def observe(self, source: HarnessStreamEvent[Any]) -> None:
        """Fold and stream the events of one Harness event, holding a fragment that later ones may continue."""
        ready: list[dict[str, Any]] = []
        for event in self.fold.events(source):
            ready += self._accept(event)
        self._publish(self.fold.fold(ready, source))

    def flush(self) -> None:
        """Fold and stream the held fragment now."""
        self._publish(self.fold.fold(self._release()))

    def boundary(self) -> None:
        """After a checkpoint commit: its display covers every event folded so far."""
        self.stream.boundary(self.fold.sequence)

    def _accept(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """The events ready for a sequence once `event` arrives; none while it extends the held fragment."""
        continued = fragment(event)
        if continued is None or self.window == 0:
            return [*self._release(), event]
        stream, text = continued
        if self.held is not None and self.held_stream == stream and self.held_length + len(text) <= MAX_MERGED_CHARS:
            extend(self.held, text)
            self.held_length += len(text)
            return []
        ready = self._release()
        self.held, self.held_stream, self.held_length = event, stream, len(text)
        self.timer = asyncio.get_running_loop().call_later(self.window, self.flush)
        return ready

    def _release(self) -> list[dict[str, Any]]:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        held, self.held = self.held, None
        return [held] if held is not None else []

    def _publish(self, observed: list[Observed]) -> None:
        for event in observed:
            self.stream.delta(event)
