"""Bounded process-local live and summary streams for Harness UI surfaces."""

from __future__ import annotations

import json
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from a13n_harness.usage import ModelUsageRecord
from a13n_stream_protocol.display import (
    BlockAppend,
    BlockPut,
    BlocksRemove,
    DisplayDelta,
    DisplayPosition,
    DisplaySnapshot,
)
from a13n_stream_protocol.projector import DisplayProjector
from ag_ui.core import CustomEvent
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
from a13n_harness_ui.mcp_apps.models import AppReference
from a13n_harness_ui.mcp_apps.snapshots import METADATA_KEY

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
    """One atomic display delta or bounded control in a root lineage."""

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
    delta: DisplayDelta | None = None


class RootStreamSummary(_StreamModel):
    """A compact producer baseline covered by the focused watch cutover."""

    thread_id: str
    run_id: str
    parent_thread_id: str | None = None
    execution_id: str | None = None
    base_continuation_id: str | None
    position: DisplayPosition
    checkpoints: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RootStreamReplay:
    summary: RootStreamSummary
    display: DisplaySnapshot
    controls: tuple[LiveEvent, ...] = ()
    block_sequences: dict[str, int] = field(default_factory=dict)

    def includes_continuation(self, continuation_id: str | None) -> bool:
        return continuation_id == self.summary.base_continuation_id or continuation_id in self.summary.checkpoints

    def chunks(self) -> Iterator[str]:
        # ASCII JSON keeps every chunk bounded in UTF-8, even for non-ASCII text.
        # Continuity is native capture bookkeeping, not browser presentation.
        encoded = json.dumps(
            {
                "display": self.display.model_dump(mode="json", exclude={"continuity"}),
                "block_sequences": self.block_sequences,
                "controls": [control.model_dump(mode="json") for control in self.controls],
            },
            ensure_ascii=True,
        )
        for start in range(0, len(encoded), 48 * 1024):
            yield encoded[start : start + 48 * 1024]


@dataclass(slots=True)
class _RootStream:
    thread_id: str
    run_id: str
    root_thread_id: str
    parent_thread_id: str | None
    execution_id: str | None
    projector: DisplayProjector
    base_continuation_id: str | None
    checkpoints: OrderedDict[str, int] = field(default_factory=OrderedDict)
    controls: OrderedDict[str, LiveEvent] = field(default_factory=OrderedDict)
    block_sequences: dict[str, int] = field(default_factory=dict)

    def capture(self) -> RootStreamReplay:
        snapshot = self.projector.capture()
        return RootStreamReplay(
            summary=RootStreamSummary(
                thread_id=self.thread_id,
                run_id=self.run_id,
                parent_thread_id=self.parent_thread_id,
                execution_id=self.execution_id,
                base_continuation_id=self.base_continuation_id,
                position=snapshot.position,
                checkpoints=dict(self.checkpoints),
            ),
            display=snapshot,
            controls=tuple(self.controls.values()),
            block_sequences=dict(self.block_sequences),
        )


class RequestContextSample(_StreamModel):
    run_id: str
    response_ordinal: int
    tokens: int


def model_usage(event: LiveEvent) -> tuple[ModelUsageRecord, ...]:
    """Read canonical model records for any agent/source in the subscribed family."""
    if event.event_type != "CUSTOM" or event.payload is None:
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
        samples.append(model)
    return tuple(samples)


def root_model_usage(event: LiveEvent) -> tuple[ModelUsageRecord, ...]:
    """Read primary root responses, retaining their original attribution after accounting resume."""
    value = event.payload.get("value") if event.payload is not None else None
    source = value.get("event") if isinstance(value, dict) else None
    payload = source.get("payload") if isinstance(source, dict) else None
    resumed_scope = (
        isinstance(value, dict)
        and value.get("run_id") == event.run_id
        and isinstance(payload, dict)
        and isinstance(payload.get("usage_id"), str)
    )
    return tuple(
        model
        for model in model_usage(event)
        if event.run_kind == "root"
        and (model.run_id == event.run_id or resumed_scope)
        and model.parent_agent_instance_id is None
        and model.delegation_id is None
        and model.source == "agent"
    )


def root_context_samples(event: LiveEvent) -> tuple[RequestContextSample, ...]:
    """Project request-local root usage, not cumulative Run usage."""
    return tuple(
        RequestContextSample(
            run_id=event.run_id,
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
        self,
        subscriber: _LiveSubscriber,
        cursor: LiveCursor,
        root_stream: RootStreamReplay | None = None,
        child_streams: tuple[RootStreamReplay, ...] = (),
        capture_display: Callable[[str, str], RootStreamReplay | None] | None = None,
    ) -> None:
        self._subscriber = subscriber
        self._cursor = cursor
        self.root_stream = root_stream
        self.child_streams = child_streams
        self._capture_display = capture_display

    def display_baseline(self, thread_id: str, run_id: str) -> RootStreamReplay | None:
        """Refresh a native subscriber from the same producer, not from raw replay."""
        if self._subscriber.root_thread_id is None or self._capture_display is None:
            return None
        return self._capture_display(thread_id, run_id)

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
        # Independent lineage retention: noisy roots cannot evict quiet roots.
        # The global ring remains for unscoped native observers. Both reference
        # the same detached events. Running/observed roots are pinned, with at
        # most sixteen additional idle roots retained for short reconnects.
        self._root_rings: OrderedDict[str, deque[LiveEvent]] = OrderedDict()
        self._root_floors: dict[str, int] = {}
        self._evicted_root_floor = 0
        self._ring_size = ring_size
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

    def register_display(
        self,
        *,
        projector: DisplayProjector,
        root_thread_id: str,
        thread_id: str,
        parent_thread_id: str | None = None,
        execution_id: str | None = None,
        base_continuation_id: str | None = None,
    ) -> None:
        """Bind delivery on the producer loop before entering native execution.

        Like the locked subscription sections, this synchronous path never yields.
        The projector owns all display semantics; the hub retains only bounded
        delivery entries and references that same producer for a fresh baseline.
        """
        run_id = projector.state.position.producer.run_id
        current = _RootStream(
            thread_id, run_id, root_thread_id, parent_thread_id, execution_id, projector, base_continuation_id
        )
        self._root_streams[thread_id] = current
        self._terminal_streams.pop(thread_id, None)

        def offer(delta: DisplayDelta) -> None:
            if self._closed or self._root_streams.get(thread_id) is not current:
                return
            for operation in delta.operations:
                if isinstance(operation, BlockPut):
                    current.block_sequences.setdefault(operation.block.id, delta.through_sequence)
                elif isinstance(operation, BlockAppend):
                    current.block_sequences.setdefault(operation.id, delta.through_sequence)
                elif isinstance(operation, BlocksRemove):
                    for block_id in operation.ids:
                        current.block_sequences.pop(block_id, None)
            # A single large native value can exceed the delta budget. Keep an
            # explicit gap, never part of an atomic operation. Bootstrap covers it.
            omitted = len(delta.model_dump_json().encode()) > 256 * 1024
            self._offer(
                run_kind="root" if parent_thread_id is None else "child",
                root_thread_id=root_thread_id,
                parent_thread_id=parent_thread_id,
                thread_id=thread_id,
                run_id=run_id,
                execution_id=execution_id,
                event_type="DISPLAY_DELTA",
                payload=None,
                payload_omitted=omitted,
                delta=None if omitted else delta,
            )

        projector.bind_delivery(offer)

    def _offer(self, **values: Any) -> LiveEvent:
        self._sequence += 1
        event = LiveEvent(epoch=self._epoch, sequence=self._sequence, **values)
        self._ring.append(event)
        root_thread_id = event.root_thread_id
        ring = self._root_rings.get(root_thread_id)
        if ring is None:
            ring = deque(maxlen=self._ring_size)
            self._root_rings[root_thread_id] = ring
            self._root_floors[root_thread_id] = self._evicted_root_floor
        if len(ring) == self._ring_size:
            self._root_floors[root_thread_id] = ring[0].sequence
        ring.append(event)
        self._root_rings.move_to_end(root_thread_id)
        stale = []
        for subscriber in self._subscribers:
            if not subscriber.accepts(event) or subscriber.gap:
                continue
            try:
                subscriber.send.send_nowait(event)
            except WouldBlock:
                subscriber.gap = True
            except (BrokenResourceError, ClosedResourceError):
                stale.append(subscriber)
        for subscriber in stale:
            self._discard_subscriber(subscriber)
        self._trim_root_rings()
        return event

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
        supplements: Sequence[AguiEvent] = (),
    ) -> None:
        """Publish small controls separately from producer-owned display output."""
        for source in (*events, *supplements):
            async with self._lock:
                if self._closed:
                    return
                current = self._root_streams.get(thread_id)
                if current is not None and current.run_id != run_id:
                    current = None
                if current is not None:
                    current.projector.flush()
                control = source.type.value in {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"} or (
                    isinstance(source, CustomEvent)
                    and source.name
                    in {
                        "a13n.harness.usage",
                        "a13n.harness_ui.checkpoint",
                        "a13n.shell.status",
                        "a13n.harness.recovery",
                    }
                )
                if not control:
                    continue
                payload, omitted = _bounded_payload(source)
                event = self._offer(
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
                if current is not None:
                    key = source.name if isinstance(source, CustomEvent) else "lifecycle"
                    if isinstance(source, CustomEvent) and isinstance(source.value, dict):
                        # Forwarded inline controls retain their source Run. A
                        # later child usage sample must not hide root context,
                        # and one process must not replace another's status.
                        source_run = source.value.get("run_id", run_id)
                        key += f":{source_run}"
                        value = source.value.get("event")
                        if source.name == "a13n.shell.status" and isinstance(value, dict):
                            key += f":{value.get('process_id')}"
                    current.controls[key] = event
                    current.controls.move_to_end(key)
                    while len(current.controls) > 64:
                        current.controls.popitem(last=False)
                    if isinstance(source, CustomEvent) and source.name == "a13n.harness_ui.checkpoint":
                        value = source.value["event"]
                        current.checkpoints[value["continuation_id"]] = value["display_sequence"]
                        while len(current.checkpoints) > 256:
                            current.checkpoints.popitem(last=False)
            await checkpoint()

    async def retains_mcp_app(self, reference: AppReference) -> bool:
        async with self._lock:
            current = self._root_streams.get(reference.thread_id)
            if current is None:
                return False
            encoded = reference.model_dump(mode="json")
            return any(
                isinstance(metadata := block.content.get("metadata"), dict)
                and isinstance(references := metadata.get(METADATA_KEY), list)
                and encoded in references
                for block in current.projector.state.blocks.values()
            )

    async def finish_root(self, *, thread_id: str, run_id: str, saved_continuation_id: str | None) -> None:
        """Release saved Runs; bound inspection of terminal unsaved output."""
        async with self._lock:
            current = self._root_streams.get(thread_id)
            if current is None or current.run_id != run_id:
                return
            if saved_continuation_id is not None:
                self._root_streams.pop(thread_id, None)
                self._terminal_streams.pop(thread_id, None)
                self._trim_root_rings()
                return
            self._terminal_streams[thread_id] = None
            self._terminal_streams.move_to_end(thread_id)
            while len(self._terminal_streams) > 16:
                expired, _ = self._terminal_streams.popitem(last=False)
                self._root_streams.pop(expired, None)
            self._trim_root_rings()

    def _trim_root_rings(self) -> None:
        pinned = {
            stream.root_thread_id for key, stream in self._root_streams.items() if key not in self._terminal_streams
        }
        pinned.update(sub.root_thread_id for sub in self._subscribers if sub.root_thread_id is not None)
        idle = [root for root in self._root_rings if root not in pinned]
        for root in idle[:-16]:
            ring = self._root_rings.pop(root)
            floor = ring[-1].sequence if ring else self._root_floors[root]
            self._evicted_root_floor = max(self._evicted_root_floor, floor)
            self._root_floors.pop(root, None)

    async def snapshot(self, *, root_thread_id: str | None = None) -> tuple[LiveEvent, ...]:
        async with self._lock:
            return tuple(
                event.model_copy(deep=True)
                for event in (self._ring if root_thread_id is None else self._root_rings.get(root_thread_id, ()))
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
            # Flush and capture every producer before allocating the cutover.
            # Callback delivery and these locked sections never yield, so no
            # operation can fall between captured coverage and subscription.
            streams = (
                tuple(
                    stream.capture()
                    for stream in self._root_streams.values()
                    if stream.root_thread_id == root_thread_id
                )
                if after is None
                else ()
            )
            start_sequence = self._validate_cursor(after, root_thread_id)
            ring = self._ring if root_thread_id is None else self._root_rings.get(root_thread_id, ())
            replay = [event for event in ring if event.sequence > start_sequence]
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
            if root_thread_id is not None and root_thread_id not in self._root_rings:
                self._root_rings[root_thread_id] = deque(maxlen=self._ring_size)
                self._root_floors[root_thread_id] = start_sequence
            subscription = LiveSubscription(
                subscriber,
                LiveCursor(epoch=self._epoch, sequence=start_sequence),
                next((stream for stream in streams if stream.summary.parent_thread_id is None), None),
                tuple(stream for stream in streams if stream.summary.parent_thread_id is not None),
                lambda thread, run: self._capture_display(root_thread_id, thread, run),
            )
        try:
            yield subscription
        finally:
            # Delivery consumers can be cancelled while the producer continues.
            # Unsubscribe is bounded local cleanup and must survive that scope.
            with CancelScope(shield=True):
                async with self._lock:
                    self._discard_subscriber(subscriber)

    def _capture_display(self, root: str | None, thread: str, run: str) -> RootStreamReplay | None:
        stream = self._root_streams.get(thread)
        return (
            stream.capture() if stream is not None and stream.run_id == run and stream.root_thread_id == root else None
        )

    def _validate_cursor(self, after: LiveCursor | None, root_thread_id: str | None) -> int:
        if after is None:
            return self._sequence
        if after.epoch != self._epoch:
            raise LivePresentationError("The detailed live epoch changed.", code="live_epoch_changed")
        if after.sequence > self._sequence:
            raise LivePresentationError("The detailed live cursor is invalid.", code="live_cursor_invalid")
        floor = (
            self._root_floors.get(root_thread_id, self._evicted_root_floor)
            if root_thread_id is not None
            else self._ring[0].sequence - 1
            if self._ring
            else self._sequence
        )
        if after.sequence < floor:
            raise LivePresentationError("The detailed live cursor is no longer retained.", code="live_cursor_expired")
        return after.sequence

    def _discard_subscriber(self, subscriber: _LiveSubscriber) -> None:
        self._subscribers.discard(subscriber)
        subscriber.send.close()
        subscriber.receive.close()
        self._trim_root_rings()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            self._ring.clear()
            self._root_streams.clear()
            self._root_rings.clear()
            self._root_floors.clear()
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
    kind: Literal[
        "configuration",
        "catalog",
        "project",
        "thread",
        "root_operation",
        "child_execution",
        "comment",
        "thread_work",
        "draft",
    ]
    root_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    execution_id: str | None = Field(default=None, min_length=1, max_length=80)
    notice: RootOperationNotice | None = None
    work_sections: tuple[Literal["tasks", "notes", "children"], ...] = ()
    work_revision: int | None = None
    run_id: str | None = None


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

    @property
    def cursor(self) -> SummaryCursor:
        """Current invalidation boundary; not an atomic cross-owner snapshot."""
        return SummaryCursor(epoch=self._epoch, sequence=self._sequence)

    async def publish(
        self,
        *,
        kind: Literal[
            "configuration",
            "catalog",
            "project",
            "thread",
            "root_operation",
            "child_execution",
            "comment",
            "thread_work",
            "draft",
        ],
        root_thread_id: str | None = None,
        thread_id: str | None = None,
        execution_id: str | None = None,
        notice: RootOperationNotice | None = None,
        work_sections: tuple[Literal["tasks", "notes", "children"], ...] = (),
        work_revision: int | None = None,
        run_id: str | None = None,
    ) -> None:
        self.publish_nowait(
            kind=kind,
            root_thread_id=root_thread_id,
            thread_id=thread_id,
            execution_id=execution_id,
            notice=notice,
            work_sections=work_sections,
            work_revision=work_revision,
            run_id=run_id,
        )

    def publish_nowait(
        self,
        *,
        kind: Literal[
            "configuration",
            "catalog",
            "project",
            "thread",
            "root_operation",
            "child_execution",
            "comment",
            "thread_work",
            "draft",
        ],
        root_thread_id: str | None = None,
        thread_id: str | None = None,
        execution_id: str | None = None,
        notice: RootOperationNotice | None = None,
        work_sections: tuple[Literal["tasks", "notes", "children"], ...] = (),
        work_revision: int | None = None,
        run_id: str | None = None,
    ) -> None:
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
            work_sections=work_sections,
            work_revision=work_revision,
            run_id=run_id,
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
        payload = _LIVE_PAYLOAD_ADAPTER.validate_python(event.model_dump(mode="json", by_alias=True))
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
