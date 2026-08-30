"""Best-effort process-local AG-UI delivery."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from ag_ui.core import Event
from anyio import BrokenResourceError, ClosedResourceError, Lock, WouldBlock, create_memory_object_stream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from a13n_ui.errors import LivePresentationError

_EVENT_ADAPTER = TypeAdapter(Event)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LiveAguiEvent(_StrictModel):
    """One event correlated within the current Host lifetime."""

    event_id: str = Field(pattern=r"^event-[0-9a-f]{32}$")
    sequence: int = Field(gt=0)
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    observed_at: datetime
    event: Event

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC)


class PresentationDelivery(_StrictModel):
    """One buffered or live process-local delivery."""

    origin: Literal["buffered", "live"]
    event: LiveAguiEvent


class EventSubscription:
    """Current buffered values followed by a best-effort live stream."""

    def __init__(
        self,
        *,
        buffered: tuple[PresentationDelivery, ...],
        receive: MemoryObjectReceiveStream[PresentationDelivery],
    ) -> None:
        self.buffered = buffered
        self.receive = receive


class SessionEventHub:
    """Keep a bounded in-memory ring and fan out live events."""

    def __init__(self, *, ring_capacity: int = 512) -> None:
        if ring_capacity < 1:
            raise ValueError("ring_capacity must be positive")
        self._ring_capacity = ring_capacity
        self._lock = Lock()
        self._next_sequence: dict[str, int] = {}
        self._rings: dict[str, deque[LiveAguiEvent]] = {}
        self._subscribers: dict[str, set[MemoryObjectSendStream[PresentationDelivery]]] = {}

    async def append(
        self,
        *,
        session_id: str,
        run_id: str | None,
        events: Sequence[Event],
    ) -> tuple[LiveAguiEvent, ...]:
        values = tuple(events)
        if not values:
            return ()
        validated: list[Event] = []
        for event in values:
            try:
                validated.append(_EVENT_ADAPTER.validate_python(event, strict=True))
            except ValidationError as exc:
                raise LivePresentationError("An AG-UI event is invalid.", code="agui_event_invalid") from exc
        async with self._lock:
            sequence = self._next_sequence.get(session_id, 1)
            now = datetime.now(UTC)
            stored = tuple(
                LiveAguiEvent(
                    event_id=f"event-{uuid4().hex}",
                    sequence=sequence + index,
                    session_id=session_id,
                    run_id=run_id,
                    observed_at=now,
                    event=event,
                )
                for index, event in enumerate(validated)
            )
            self._next_sequence[session_id] = sequence + len(stored)
            ring = self._rings.setdefault(session_id, deque(maxlen=self._ring_capacity))
            ring.extend(stored)
            stale: list[MemoryObjectSendStream[PresentationDelivery]] = []
            for send in self._subscribers.get(session_id, ()):
                try:
                    for item in stored:
                        send.send_nowait(PresentationDelivery(origin="live", event=item))
                except WouldBlock:
                    continue
                except (BrokenResourceError, ClosedResourceError):
                    stale.append(send)
            subscribers = self._subscribers.get(session_id)
            if subscribers is not None:
                for send in stale:
                    subscribers.discard(send)
                if not subscribers:
                    self._subscribers.pop(session_id, None)
            return stored

    @asynccontextmanager
    async def subscribe(
        self,
        session_id: str,
        *,
        capacity: int = 256,
    ) -> AsyncGenerator[EventSubscription]:
        if capacity < 1:
            raise ValueError("subscription capacity must be positive")
        send, receive = create_memory_object_stream[PresentationDelivery](capacity)
        async with self._lock:
            buffered = tuple(
                PresentationDelivery(origin="buffered", event=item) for item in self._rings.get(session_id, ())
            )
            self._subscribers.setdefault(session_id, set()).add(send)
        try:
            yield EventSubscription(buffered=buffered, receive=receive)
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(send)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)
            await send.aclose()
            await receive.aclose()

    async def close(self) -> None:
        async with self._lock:
            subscribers = tuple(send for values in self._subscribers.values() for send in values)
            self._subscribers.clear()
            self._rings.clear()
            self._next_sequence.clear()
        for send in subscribers:
            await send.aclose()


__all__ = [
    "EventSubscription",
    "LiveAguiEvent",
    "PresentationDelivery",
    "SessionEventHub",
]
