"""Coalescing an attempt's output: consecutive fragments of one stream become one stream event.

Model deltas carry a token or a few, so streaming each as its own event would append thousands of entries per step.
The coalescer holds one fragment event (text, reasoning or tool-call arguments, see `display.fragment`) and extends it
with the fragments that continue it until another event arrives, the window elapses, a boundary needs the display, or
the attempt ends. Only then does the event get its sequence, so sequences stay dense, and folding the merged event
leaves the display's items as folding its parts would, apart from the stream positions they record.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol.display import bound_event
from ag_ui.core import ToolCallArgsEvent, ToolCallResultEvent

from a13n_service.runs.display import DisplayFold, extend, fragment
from a13n_service.runs.stream import ThreadStream

# The size in which the observer splits long input text: merging never rebuilds what it split, and a merged event
# stays far below the stream's per-delta limit.
MAX_MERGED_CHARS = 8192


@dataclass
class _Event:
    """The complete display value and the unchanged bounded transport value at the same sequence."""

    content: dict[str, Any]
    transport: dict[str, Any]

    @classmethod
    def of(cls, content: dict[str, Any]) -> "_Event":
        model = {"TOOL_CALL_ARGS": ToolCallArgsEvent, "TOOL_CALL_RESULT": ToolCallResultEvent}.get(content["type"])
        transport = (
            bound_event(model.model_validate(content)).model_dump(mode="json", by_alias=True) if model else content
        )
        return cls(content, transport)


class Coalescer:
    """The path from an attempt's Harness events to its display fold and thread stream. It holds at most one
    fragment event, for at most `window` seconds from the fragment that started it; a window of 0 holds none."""

    def __init__(self, fold: DisplayFold, stream: ThreadStream, *, window: float) -> None:
        self.fold, self.stream, self.window = fold, stream, window
        self.held: _Event | None = None
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
        ready: list[_Event] = []
        for event in self.fold.events(source):
            ready += self._accept(_Event.of(event))
        self._publish(ready, source)

    def flush(self) -> None:
        """Fold and stream the held fragment now."""
        self._publish(self._release())

    def boundary(self) -> None:
        """After a checkpoint commit: its display covers every event folded so far."""
        self.stream.boundary(self.fold.sequence)

    def _accept(self, event: _Event) -> list[_Event]:
        """The events ready for a sequence once `event` arrives; none while it extends the held fragment."""
        continued = fragment(event.transport)
        if continued is None or self.window == 0:
            return [*self._release(), event]
        stream, text = continued
        if self.held is not None and self.held_stream == stream and self.held_length + len(text) <= MAX_MERGED_CHARS:
            extend(self.held.content, fragment(event.content)[1])  # type: ignore[index]
            if self.held.transport is not self.held.content:
                extend(self.held.transport, text)
            self.held_length += len(text)
            return []
        ready = self._release()
        self.held, self.held_stream, self.held_length = event, stream, len(text)
        self.timer = asyncio.get_running_loop().call_later(self.window, self.flush)
        return ready

    def _release(self) -> list[_Event]:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        held, self.held = self.held, None
        return [held] if held is not None else []

    def _publish(self, events: list[_Event], source: HarnessStreamEvent[Any] | None = None) -> None:
        observed = self.fold.fold([event.content for event in events], source)
        for event, value in zip(events, observed, strict=True):
            self.stream.delta(value.model_copy(update={"event": event.transport}))
