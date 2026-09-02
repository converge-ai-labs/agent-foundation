"""Bounded process-local live event fan-out for Agent UI surfaces."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Literal

from ag_ui.core import Event as AguiEvent
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    Lock,
    WouldBlock,
    create_memory_object_stream,
)
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

_LIVE_PAYLOAD_ADAPTER = TypeAdapter(dict[str, JsonValue])
_DEFAULT_RING_SIZE = 256
_DEFAULT_SUBSCRIBER_BUFFER_SIZE = 64
_MAX_EVENT_BYTES = 64 * 1024


class LiveEvent(BaseModel):
    """Detached bounded AG-UI event correlated to one root or child Run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    sequence: int = Field(ge=1)
    run_kind: Literal["root", "child"]
    thread_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)
    execution_id: str | None = Field(default=None, min_length=1, max_length=80)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, JsonValue] | None
    payload_omitted: bool


class LiveSubscription:
    """One App-owned best-effort live stream."""

    def __init__(self, receive: MemoryObjectReceiveStream[LiveEvent]) -> None:
        self._receive = receive

    def __aiter__(self) -> AsyncIterator[LiveEvent]:
        return self

    async def __anext__(self) -> LiveEvent:
        try:
            return await self.receive()
        except (ClosedResourceError, EndOfStream):
            raise StopAsyncIteration from None

    async def receive(self) -> LiveEvent:
        """Wait for the next retained or live event."""

        event = await self._receive.receive()
        return event.model_copy(deep=True)


class _Subscriber:
    def __init__(
        self,
        *,
        send: MemoryObjectSendStream[LiveEvent],
        receive: MemoryObjectReceiveStream[LiveEvent],
        thread_id: str | None,
    ) -> None:
        self.send = send
        self.receive = receive
        self.thread_id = thread_id

    def accepts(self, event: LiveEvent) -> bool:
        return self.thread_id is None or self.thread_id == event.thread_id


class AgentUiLiveHub:
    """Keep a small ring and fan out without waiting on subscribers."""

    def __init__(
        self,
        *,
        ring_size: int = _DEFAULT_RING_SIZE,
        subscriber_buffer_size: int = _DEFAULT_SUBSCRIBER_BUFFER_SIZE,
    ) -> None:
        if ring_size < 1 or subscriber_buffer_size < 1:
            raise ValueError("live hub bounds must be positive")
        self._ring: deque[LiveEvent] = deque(maxlen=ring_size)
        self._subscriber_buffer_size = subscriber_buffer_size
        self._subscribers: set[_Subscriber] = set()
        self._sequence = 0
        self._closed = False
        self._lock = Lock()

    async def publish(
        self,
        *,
        run_kind: Literal["root", "child"],
        thread_id: str,
        run_id: str,
        events: Sequence[AguiEvent],
        execution_id: str | None = None,
    ) -> None:
        """Publish detached events while dropping old work for slow subscribers."""

        async with self._lock:
            if self._closed:
                return
            stale: list[_Subscriber] = []
            for source in events:
                self._sequence += 1
                payload, omitted = _bounded_payload(source)
                event = LiveEvent(
                    sequence=self._sequence,
                    run_kind=run_kind,
                    thread_id=thread_id,
                    run_id=run_id,
                    execution_id=execution_id,
                    event_type=str(source.type),
                    payload=payload,
                    payload_omitted=omitted,
                )
                self._ring.append(event.model_copy(deep=True))
                for subscriber in self._subscribers:
                    if not subscriber.accepts(event):
                        continue
                    try:
                        subscriber.send.send_nowait(event.model_copy(deep=True))
                    except WouldBlock:
                        try:
                            subscriber.receive.receive_nowait()
                            subscriber.send.send_nowait(event.model_copy(deep=True))
                        except WouldBlock:
                            pass
                        except (BrokenResourceError, ClosedResourceError, EndOfStream):
                            stale.append(subscriber)
                    except (BrokenResourceError, ClosedResourceError):
                        stale.append(subscriber)
            for subscriber in stale:
                self._subscribers.discard(subscriber)
                subscriber.send.close()
                subscriber.receive.close()

    async def snapshot(
        self,
        *,
        thread_id: str | None = None,
    ) -> tuple[LiveEvent, ...]:
        """Return a detached filtered view of the current ring."""

        async with self._lock:
            return tuple(
                event.model_copy(deep=True) for event in self._ring if thread_id is None or event.thread_id == thread_id
            )

    @asynccontextmanager
    async def subscribe(
        self,
        *,
        thread_id: str | None = None,
    ) -> AsyncGenerator[LiveSubscription]:
        """Replay the matching ring and follow future best-effort events."""

        capacity = max(len(self._ring), self._subscriber_buffer_size)
        send, receive = create_memory_object_stream[LiveEvent](capacity)
        subscriber = _Subscriber(
            send=send,
            receive=receive,
            thread_id=thread_id,
        )
        async with self._lock:
            if self._closed:
                send.close()
            else:
                matching = [event for event in self._ring if subscriber.accepts(event)]
                for event in matching[-capacity:]:
                    send.send_nowait(event.model_copy(deep=True))
                self._subscribers.add(subscriber)
        try:
            yield LiveSubscription(receive)
        finally:
            async with self._lock:
                self._subscribers.discard(subscriber)
                send.close()
                receive.close()

    async def close(self) -> None:
        """Close all subscribers and discard transient retained events."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            self._ring.clear()
            for subscriber in subscribers:
                subscriber.send.close()
                subscriber.receive.close()


def _bounded_payload(event: AguiEvent) -> tuple[dict[str, JsonValue] | None, bool]:
    payload = _LIVE_PAYLOAD_ADAPTER.validate_python(event.model_dump(mode="json"))
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_EVENT_BYTES:
        return None, True
    return payload, False


__all__ = ["AgentUiLiveHub", "LiveEvent", "LiveSubscription"]
