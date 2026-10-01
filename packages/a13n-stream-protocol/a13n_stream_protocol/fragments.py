"""Lossless transport fragmentation for content-bearing CUSTOM events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ag_ui.core.events import CustomEvent

FRAGMENT_EVENT_NAME = "a13n.stream.fragment"


def fragment_custom_event(event: CustomEvent, *, identity: str) -> list[CustomEvent]:
    """Keep small events intact; split oversized JSON without changing its schema."""
    encoded = event.model_dump_json(by_alias=True)
    if len(encoded.encode("utf-8")) <= 48 * 1024:
        return [event]
    # Double JSON escaping and non-ASCII still leave room below 64 KiB per frame.
    size = 4096
    count = (len(encoded) + size - 1) // size
    return [
        CustomEvent(
            timestamp=event.timestamp,
            subagent_run_id=event.subagent_run_id,
            name=FRAGMENT_EVENT_NAME,
            value={"id": identity, "index": index, "count": count, "data": encoded[offset : offset + size]},
        )
        for index, offset in enumerate(range(0, len(encoded), size))
    ]


@dataclass(slots=True)
class _Assembly:
    count: int
    parts: list[str] = field(default_factory=list)
    size: int = 0


class CustomEventAssembler:
    """Reassemble bounded ordered frames; gaps never produce partial domain events.

    One instance belongs to one live subscription. Reconnects start fresh. A
    caller may display its existing observation-gap notice when a frame is lost.
    """

    def __init__(self, *, max_bytes: int = 64 * 1024 * 1024, max_pending: int = 8) -> None:
        if max_bytes < 1 or max_pending < 1:
            raise ValueError("custom event assembly bounds must be positive")
        self.max_bytes = max_bytes
        self.max_pending = max_pending
        self._pending: dict[str, _Assembly] = {}
        self._size = 0
        self.gap = False

    def _discard(self, identity: str) -> None:
        assembly = self._pending.pop(identity, None)
        if assembly is not None:
            self._size -= assembly.size

    def accept(self, payload: Mapping[str, object]) -> Mapping[str, object] | None:
        if payload.get("name") != FRAGMENT_EVENT_NAME:
            return payload
        value = payload.get("value")
        if not isinstance(value, dict):
            self.gap = True
            return None
        identity, index, count, data = (value.get(key) for key in ("id", "index", "count", "data"))
        if not (
            isinstance(identity, str)
            and isinstance(index, int)
            and isinstance(count, int)
            and 0 <= index < count
            and isinstance(data, str)
        ):
            self.gap = True
            return None
        if index == 0:
            self._discard(identity)
            while len(self._pending) >= self.max_pending:
                self._discard(next(iter(self._pending)))
                self.gap = True
            self._pending[identity] = _Assembly(count)
        assembly = self._pending.get(identity)
        size = len(data.encode("utf-8"))
        if (
            assembly is None
            or assembly.count != count
            or len(assembly.parts) != index
            or self._size + size > self.max_bytes
        ):
            self._discard(identity)
            self.gap = True
            return None
        assembly.parts.append(data)
        assembly.size += size
        self._size += size
        if len(assembly.parts) != count:
            return None
        self._discard(identity)
        try:
            event = CustomEvent.model_validate_json("".join(assembly.parts))
        except ValueError:
            self.gap = True
            return None
        if event.name == FRAGMENT_EVENT_NAME:
            self.gap = True
            return None
        return event.model_dump(mode="json", by_alias=True)
