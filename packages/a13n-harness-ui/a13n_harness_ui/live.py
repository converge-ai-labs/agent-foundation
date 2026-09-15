"""Bounded process-local live and summary streams for Harness UI surfaces."""

from __future__ import annotations

import json
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from a13n_harness.usage import ModelUsageRecord
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event as AguiEvent
from anyio import (
    BrokenResourceError,
    CancelScope,
    ClosedResourceError,
    EndOfStream,
    Lock,
    WouldBlock,
    create_memory_object_stream,
)
from anyio.lowlevel import checkpoint
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from a13n_harness_ui.errors import LivePresentationError

_LIVE_PAYLOAD_ADAPTER = TypeAdapter(dict[str, JsonValue])
_DEFAULT_RING_SIZE = 256
_DEFAULT_SUBSCRIBER_BUFFER_SIZE = 64
_MAX_EVENT_BYTES = 64 * 1024


class _StreamModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LiveCursor(_StreamModel):
    epoch: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=0)


class LiveEvent(_StreamModel):
    """Detached bounded AG-UI event correlated to one complete root lineage."""

    epoch: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    run_kind: Literal["root", "child"]
    root_thread_id: str = Field(min_length=1, max_length=80)
    parent_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)
    execution_id: str | None = Field(default=None, min_length=1, max_length=80)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, JsonValue] | None
    payload_omitted: bool


class RootStreamSummary(_StreamModel):
    """Finite observer prefix covered by a focused watch's cutover."""

    thread_id: str
    run_id: str
    base_continuation_id: str | None
    event_count: int = Field(ge=0)


class RootStreamEvent(_StreamModel):
    index: int = Field(ge=0)
    event_type: str
    payload: dict[str, JsonValue] | None
    payload_omitted: bool


@dataclass(frozen=True, slots=True)
class RootStreamReplay:
    summary: RootStreamSummary
    observer: HarnessAguiObserver

    def batches(self) -> Iterator[tuple[RootStreamEvent, ...]]:
        """Read only the captured prefix; do not copy an entire Run per page."""
        for start in range(0, self.summary.event_count, 16):
            events = self.observer.snapshot(start=start, stop=min(start + 16, self.summary.event_count))
            batch = []
            for index, event in enumerate(events, start):
                payload, omitted = _bounded_payload(event)
                batch.append(
                    RootStreamEvent(index=index, event_type=event.type.value, payload=payload, payload_omitted=omitted)
                )
            yield tuple(batch)


@dataclass(slots=True)
class _RootStream:
    thread_id: str
    run_id: str
    observer: HarnessAguiObserver
    base_continuation_id: str | None
    published_count: int = 0

    def capture(self) -> RootStreamReplay:
        return RootStreamReplay(
            summary=RootStreamSummary(
                thread_id=self.thread_id,
                run_id=self.run_id,
                base_continuation_id=self.base_continuation_id,
                event_count=self.published_count,
            ),
            observer=self.observer,
        )


class RequestContextSample(_StreamModel):
    run_id: str
    response_ordinal: int
    tokens: int


def root_model_usage(event: LiveEvent) -> tuple[ModelUsageRecord, ...]:
    """Read attributed root model records, excluding children and provider totals."""
    if event.run_kind != "root" or event.event_type != "CUSTOM" or event.payload is None:
        return ()
    value = event.payload.get("value")
    source = value.get("event") if isinstance(value, dict) else None
    payload = source.get("payload") if isinstance(source, dict) else None
    if not isinstance(payload, dict) or payload.get("type") != "usage_report":
        return ()
    records = payload.get("records")
    if not isinstance(records, list):
        return ()
    samples = []
    for record in records:
        try:
            model = ModelUsageRecord.model_validate(record)
        except ValidationError:
            continue
        if (
            model.run_id != event.run_id
            or model.parent_agent_instance_id is not None
            or model.delegation_id is not None
        ):
            continue
        samples.append(model)
    return tuple(samples)


def root_context_samples(event: LiveEvent) -> tuple[RequestContextSample, ...]:
    """Project request-local root usage, not cumulative Run usage."""
    return tuple(
        RequestContextSample(
            run_id=model.run_id,
            response_ordinal=model.response_ordinal,
            tokens=model.request_usage.input_tokens + model.request_usage.output_tokens,
        )
        for model in root_model_usage(event)
    )


class _LiveSubscriber:
    def __init__(
        self,
        *,
        send: MemoryObjectSendStream[LiveEvent],
        receive: MemoryObjectReceiveStream[LiveEvent],
        root_thread_id: str | None,
    ) -> None:
        self.send = send
        self.receive = receive
        self.root_thread_id = root_thread_id
        self.gap = False

    def accepts(self, event: LiveEvent) -> bool:
        return self.root_thread_id is None or self.root_thread_id == event.root_thread_id


class LiveSubscription:
    """One App-owned best-effort detailed stream with an explicit cutover."""

    def __init__(
        self, subscriber: _LiveSubscriber, cursor: LiveCursor, root_stream: RootStreamReplay | None = None
    ) -> None:
        self._subscriber = subscriber
        self._cursor = cursor
        self.root_stream = root_stream

    @property
    def cursor(self) -> LiveCursor:
        return self._cursor.model_copy(deep=True)

    def __aiter__(self) -> AsyncIterator[LiveEvent]:
        return self

    async def __anext__(self) -> LiveEvent:
        try:
            return await self.receive()
        except (ClosedResourceError, EndOfStream):
            raise StopAsyncIteration from None

    async def receive(self) -> LiveEvent:
        if self._subscriber.gap:
            raise LivePresentationError(
                "The detailed live subscriber fell behind its bounded buffer.",
                code="live_cursor_expired",
            )
        event = await self._subscriber.receive.receive()
        if self._subscriber.gap:
            raise LivePresentationError(
                "The detailed live subscriber fell behind its bounded buffer.",
                code="live_cursor_expired",
            )
        return event.model_copy(deep=True)

    def drain_pending(self) -> tuple[LiveEvent, ...]:
        """Drain already delivered events after the producer has completed."""
        if self._subscriber.gap:
            raise LivePresentationError("Detailed events were dropped.", code="live_cursor_expired")
        events: list[LiveEvent] = []
        while True:
            try:
                events.append(self._subscriber.receive.receive_nowait().model_copy(deep=True))
            except (WouldBlock, EndOfStream, ClosedResourceError):
                return tuple(events)


class HarnessUiLiveHub:
    """Keep a small detailed ring and fan out complete root lineages without waiting."""

    def __init__(
        self,
        *,
        epoch: str | None = None,
        ring_size: int = _DEFAULT_RING_SIZE,
        subscriber_buffer_size: int = _DEFAULT_SUBSCRIBER_BUFFER_SIZE,
    ) -> None:
        if ring_size < 1 or subscriber_buffer_size < 1:
            raise ValueError("live hub bounds must be positive")
        self._epoch = epoch or f"live-{uuid4().hex}"
        self._ring: deque[LiveEvent] = deque(maxlen=ring_size)
        self._subscriber_buffer_size = subscriber_buffer_size
        self._subscribers: set[_LiveSubscriber] = set()
        self._sequence = 0
        self._closed = False
        self._lock = Lock()
        self._root_streams: dict[str, _RootStream] = {}
        self._terminal_streams: OrderedDict[str, None] = OrderedDict()

    @property
    def epoch(self) -> str:
        return self._epoch

    async def publish(
        self,
        *,
        run_kind: Literal["root", "child"],
        root_thread_id: str,
        parent_thread_id: str | None,
        thread_id: str,
        run_id: str,
        events: Sequence[AguiEvent],
        execution_id: str | None = None,
        observer: HarnessAguiObserver | None = None,
        base_continuation_id: str | None = None,
    ) -> None:
        """Publish detached events while marking slow subscribers for reset.

        The root producer supplies its existing observer and its latest batch.
        Only the published prefix becomes visible to new subscriptions, even if
        observation precedes asynchronous publication.
        """
        start = 0 if observer is None else observer.event_count - len(events)
        if observer is not None and (run_kind != "root" or start < 0):
            raise ValueError("observer replay requires the root's latest observed batch")
        for index, source in enumerate(events, start):
            async with self._lock:
                if self._closed:
                    return
                stale: list[_LiveSubscriber] = []
                self._sequence += 1
                payload, omitted = _bounded_payload(source)
                event = LiveEvent(
                    epoch=self._epoch,
                    sequence=self._sequence,
                    run_kind=run_kind,
                    root_thread_id=root_thread_id,
                    parent_thread_id=parent_thread_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    execution_id=execution_id,
                    event_type=source.type.value,
                    payload=payload,
                    payload_omitted=omitted,
                )
                self._ring.append(event.model_copy(deep=True))
                if observer is not None:
                    current = self._root_streams.get(thread_id)
                    if current is None or current.run_id != run_id:
                        current = _RootStream(thread_id, run_id, observer, base_continuation_id)
                        self._root_streams[thread_id] = current
                        self._terminal_streams.pop(thread_id, None)
                    current.published_count = index + 1
                for subscriber in self._subscribers:
                    if not subscriber.accepts(event) or subscriber.gap:
                        continue
                    try:
                        subscriber.send.send_nowait(event.model_copy(deep=True))
                    except WouldBlock:
                        subscriber.gap = True
                    except (BrokenResourceError, ClosedResourceError):
                        stale.append(subscriber)
                for subscriber in stale:
                    self._discard_subscriber(subscriber)
            # A large framed event must not overflow even a ready consumer merely
            # because its producer submitted one batch. Never await under the lock.
            await checkpoint()

    async def finish_root(self, *, thread_id: str, run_id: str, saved_continuation_id: str | None) -> None:
        """Release saved Runs; bound inspection of terminal unsaved output."""
        async with self._lock:
            current = self._root_streams.get(thread_id)
            if current is None or current.run_id != run_id:
                return
            if saved_continuation_id is not None:
                self._root_streams.pop(thread_id, None)
                self._terminal_streams.pop(thread_id, None)
                return
            self._terminal_streams[thread_id] = None
            self._terminal_streams.move_to_end(thread_id)
            while len(self._terminal_streams) > 256:
                expired, _ = self._terminal_streams.popitem(last=False)
                self._root_streams.pop(expired, None)

    async def snapshot(self, *, root_thread_id: str | None = None) -> tuple[LiveEvent, ...]:
        async with self._lock:
            return tuple(
                event.model_copy(deep=True)
                for event in self._ring
                if root_thread_id is None or event.root_thread_id == root_thread_id
            )

    @asynccontextmanager
    async def subscribe(
        self,
        *,
        root_thread_id: str | None = None,
        after: LiveCursor | None = None,
    ) -> AsyncGenerator[LiveSubscription]:
        """Install a subscriber atomically at the current or supplied sequence cutover."""

        async with self._lock:
            if self._closed:
                raise LivePresentationError("The detailed live hub is closed.", code="live_unavailable")
            start_sequence = self._validate_cursor(after)
            replay = [
                event
                for event in self._ring
                if event.sequence > start_sequence
                and (root_thread_id is None or event.root_thread_id == root_thread_id)
            ]
            capacity = max(len(replay), self._subscriber_buffer_size)
            send, receive = create_memory_object_stream[LiveEvent](capacity)
            subscriber = _LiveSubscriber(
                send=send,
                receive=receive,
                root_thread_id=root_thread_id,
            )
            for event in replay:
                send.send_nowait(event.model_copy(deep=True))
            self._subscribers.add(subscriber)
            root_stream = self._root_streams.get(root_thread_id or "")
            subscription = LiveSubscription(
                subscriber,
                LiveCursor(epoch=self._epoch, sequence=start_sequence),
                root_stream.capture() if root_stream is not None and after is None else None,
            )
        try:
            yield subscription
        finally:
            # Delivery consumers can be cancelled while the producer continues.
            # Unsubscribe is bounded local cleanup and must survive that scope.
            with CancelScope(shield=True):
                async with self._lock:
                    self._discard_subscriber(subscriber)

    def _validate_cursor(self, after: LiveCursor | None) -> int:
        if after is None:
            return self._sequence
        if after.epoch != self._epoch:
            raise LivePresentationError("The detailed live epoch changed.", code="live_epoch_changed")
        if after.sequence > self._sequence:
            raise LivePresentationError("The detailed live cursor is invalid.", code="live_cursor_invalid")
        if self._ring and after.sequence < self._ring[0].sequence - 1:
            raise LivePresentationError("The detailed live cursor is no longer retained.", code="live_cursor_expired")
        return after.sequence

    def _discard_subscriber(self, subscriber: _LiveSubscriber) -> None:
        self._subscribers.discard(subscriber)
        subscriber.send.close()
        subscriber.receive.close()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            self._ring.clear()
            self._root_streams.clear()
            self._terminal_streams.clear()
            for subscriber in subscribers:
                subscriber.send.close()
                subscriber.receive.close()


class SummaryCursor(_StreamModel):
    epoch: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=0)


class RootOperationNotice(_StreamModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "failed", "suspended"]
    brief: str = Field(min_length=1, max_length=320)


class SummaryInvalidation(_StreamModel):
    epoch: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    kind: Literal["configuration", "catalog", "project", "thread", "root_operation", "child_execution", "comment"]
    root_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    execution_id: str | None = Field(default=None, min_length=1, max_length=80)
    notice: RootOperationNotice | None = None


class _SummarySubscriber:
    def __init__(
        self,
        send: MemoryObjectSendStream[SummaryInvalidation],
        receive: MemoryObjectReceiveStream[SummaryInvalidation],
    ) -> None:
        self.send = send
        self.receive = receive
        self.gap = False


class SummarySubscription:
    def __init__(self, subscriber: _SummarySubscriber, cursor: SummaryCursor) -> None:
        self._subscriber = subscriber
        self._cursor = cursor

    @property
    def cursor(self) -> SummaryCursor:
        return self._cursor.model_copy(deep=True)

    def __aiter__(self) -> AsyncIterator[SummaryInvalidation]:
        return self

    async def __anext__(self) -> SummaryInvalidation:
        try:
            return await self.receive()
        except (ClosedResourceError, EndOfStream):
            raise StopAsyncIteration from None

    async def receive(self) -> SummaryInvalidation:
        if self._subscriber.gap:
            raise LivePresentationError(
                "The summary subscriber fell behind its bounded buffer.",
                code="summary_cursor_expired",
            )
        event = await self._subscriber.receive.receive()
        if self._subscriber.gap:
            raise LivePresentationError(
                "The summary subscriber fell behind its bounded buffer.",
                code="summary_cursor_expired",
            )
        return event.model_copy(deep=True)


class HarnessUiSummaryHub:
    """Fan out lightweight App-wide refetch hints independently from detailed live payloads."""

    def __init__(
        self,
        *,
        epoch: str,
        ring_size: int = _DEFAULT_RING_SIZE,
        subscriber_buffer_size: int = _DEFAULT_SUBSCRIBER_BUFFER_SIZE,
    ) -> None:
        if ring_size < 1 or subscriber_buffer_size < 1:
            raise ValueError("summary hub bounds must be positive")
        self._epoch = epoch
        self._ring: deque[SummaryInvalidation] = deque(maxlen=ring_size)
        self._subscriber_buffer_size = subscriber_buffer_size
        self._subscribers: set[_SummarySubscriber] = set()
        self._sequence = 0
        self._closed = False
        self._lock = Lock()

    async def publish(
        self,
        *,
        kind: Literal["configuration", "catalog", "project", "thread", "root_operation", "child_execution", "comment"],
        root_thread_id: str | None = None,
        thread_id: str | None = None,
        execution_id: str | None = None,
        notice: RootOperationNotice | None = None,
    ) -> None:
        async with self._lock:
            if self._closed:
                return
            self._sequence += 1
            event = SummaryInvalidation(
                epoch=self._epoch,
                sequence=self._sequence,
                kind=kind,
                root_thread_id=root_thread_id,
                thread_id=thread_id,
                execution_id=execution_id,
                notice=notice,
            )
            self._ring.append(event)
            stale: list[_SummarySubscriber] = []
            for subscriber in self._subscribers:
                if subscriber.gap:
                    continue
                try:
                    subscriber.send.send_nowait(event.model_copy(deep=True))
                except WouldBlock:
                    subscriber.gap = True
                except (BrokenResourceError, ClosedResourceError):
                    stale.append(subscriber)
            for subscriber in stale:
                self._discard_subscriber(subscriber)

    @asynccontextmanager
    async def subscribe(self, *, after: SummaryCursor | None = None) -> AsyncGenerator[SummarySubscription]:
        async with self._lock:
            if self._closed:
                raise LivePresentationError("The summary hub is closed.", code="summary_unavailable")
            start_sequence = self._validate_cursor(after)
            replay = [event for event in self._ring if event.sequence > start_sequence]
            capacity = max(len(replay), self._subscriber_buffer_size)
            send, receive = create_memory_object_stream[SummaryInvalidation](capacity)
            subscriber = _SummarySubscriber(send, receive)
            for event in replay:
                send.send_nowait(event.model_copy(deep=True))
            self._subscribers.add(subscriber)
            subscription = SummarySubscription(
                subscriber,
                SummaryCursor(epoch=self._epoch, sequence=start_sequence),
            )
        try:
            yield subscription
        finally:
            # Delivery consumers can be cancelled while the producer continues.
            # Unsubscribe is bounded local cleanup and must survive that scope.
            with CancelScope(shield=True):
                async with self._lock:
                    self._discard_subscriber(subscriber)

    def _validate_cursor(self, after: SummaryCursor | None) -> int:
        if after is None:
            return self._sequence
        if after.epoch != self._epoch:
            raise LivePresentationError("The summary epoch changed.", code="summary_epoch_changed")
        if after.sequence > self._sequence:
            raise LivePresentationError("The summary cursor is invalid.", code="summary_cursor_invalid")
        if self._ring and after.sequence < self._ring[0].sequence - 1:
            raise LivePresentationError("The summary cursor is no longer retained.", code="summary_cursor_expired")
        return after.sequence

    def _discard_subscriber(self, subscriber: _SummarySubscriber) -> None:
        self._subscribers.discard(subscriber)
        subscriber.send.close()
        subscriber.receive.close()

    async def close(self) -> None:
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
    try:
        payload = _LIVE_PAYLOAD_ADAPTER.validate_python(event.model_dump(mode="json"))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, ValidationError):
        return None, True
    if len(encoded) > _MAX_EVENT_BYTES:
        return None, True
    return payload, False


__all__ = [
    "HarnessUiLiveHub",
    "HarnessUiSummaryHub",
    "LiveCursor",
    "LiveEvent",
    "LiveSubscription",
    "SummaryCursor",
    "SummaryInvalidation",
    "SummarySubscription",
]
