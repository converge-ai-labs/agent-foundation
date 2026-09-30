"""Stateful observation of public Harness streams as AG-UI events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from a13n_harness import (
    AgentStreamEventProtocol,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessStreamEvent,
)
from a13n_harness.content import ContentItem, request_input_content
from a13n_harness.events import ToolExtraEventPayload
from a13n_harness.model_context import ModelInputEvent
from a13n_harness.tools._output import tool_execution_value
from ag_ui.core import Event
from ag_ui.core.events import (
    BaseEvent,
    CustomEvent,
    ReasoningEncryptedValueEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedSuccessOutcome,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TokenUsage,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic import JsonValue, TypeAdapter, ValidationError
from pydantic_ai.messages import (
    CapabilityEvent,
    DeferredToolResultsEvent,
    EnqueuedMessagesEvent,
    FunctionToolResultEvent,
    ModelRequest,
    OutputToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UnknownCapabilityEvent,
    UserContent,
)
from pydantic_ai.tools import DeferredToolRequests

from a13n_stream_protocol.fragments import fragment_custom_event
from a13n_stream_protocol.messages import ContentMetadata, project_input_content

_AGUI_EVENT_ADAPTER = TypeAdapter(Event)
_ANY_ADAPTER = TypeAdapter(Any)
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)
_SOURCE_CORRELATION_FIELDS = ("thread_id", "run_id", "sequence", "occurred_at")
_MUTABLE_FIELDS_BY_EVENT_TYPE: dict[object, frozenset[str]] = {
    TextMessageContentEvent.model_fields["type"].default: frozenset({"delta"}),
    ReasoningMessageContentEvent.model_fields["type"].default: frozenset({"delta"}),
    ReasoningEncryptedValueEvent.model_fields["type"].default: frozenset({"encrypted_value"}),
    ToolCallArgsEvent.model_fields["type"].default: frozenset({"delta"}),
    ToolCallResultEvent.model_fields["type"].default: frozenset({"content"}),
    RunFinishedEvent.model_fields["type"].default: frozenset({"result"}),
    RunErrorEvent.model_fields["type"].default: frozenset({"message"}),
    CustomEvent.model_fields["type"].default: frozenset(),
}

type AguiEventProcessor = Callable[[HarnessStreamEvent[Any], Event], Event | None]


class AguiObservationError(ValueError):
    """A public Harness item could not be observed without changing its meaning."""


@dataclass(slots=True)
class _PartCursor:
    kind: Literal["text", "reasoning", "tool_call"]
    part_id: str
    tool_name: str | None = None
    emitted_content: bool = False
    emitted_signature: bool = False


@dataclass(slots=True)
class _ObserverState:
    request_index: int = 0
    parts: dict[int, _PartCursor] = field(default_factory=dict)


class HarnessAguiObserver:
    """Convert and accumulate one public Harness run as typed AG-UI events."""

    def __init__(self, *, processor: AguiEventProcessor | None = None) -> None:
        self._processor = processor
        self._thread_id: str | None = None
        self._run_id: str | None = None
        self._state = _ObserverState()
        self._events: list[Event] = []
        self._resuming = False
        self._resume_completed = False

    @property
    def thread_id(self) -> str | None:
        """Return the bound Harness Thread identity, if observation has begun."""
        return self._thread_id

    @property
    def run_id(self) -> str | None:
        """Return the bound Harness Run identity, if observation has begun."""
        return self._run_id

    async def resume(self, history: AsyncIterable[HarnessStreamEvent[Any]]) -> None:
        """Atomically rebuild this fresh observer from finite source history.

        Historical events are accumulated but not returned. Observation and
        another resumption are rejected until the history iterable finishes.
        """
        if not isinstance(history, AsyncIterable):
            raise TypeError("history must be an async iterable of Harness stream events")
        if self._resuming:
            raise AguiObservationError("Observer resumption is already in progress")
        if self._resume_completed or self._thread_id is not None or self._run_id is not None:
            raise AguiObservationError("Observer resumption requires a fresh observer")

        staged = HarnessAguiObserver(processor=self._processor)
        self._resuming = True
        try:
            async for item in history:
                staged.observe(item)
            self._thread_id = staged._thread_id
            self._run_id = staged._run_id
            self._state = staged._state
            self._events = staged._events
            self._resume_completed = True
        finally:
            self._resuming = False

    def observe(self, item: HarnessStreamEvent[Any]) -> tuple[Event, ...]:
        """Convert and atomically accumulate one source item."""
        if self._resuming:
            raise AguiObservationError("Cannot observe while observer resumption is in progress")
        if not isinstance(item, HarnessEvent | HarnessRunResultEvent):
            raise TypeError("item must be a HarnessEvent or HarnessRunResultEvent")
        self._validate_correlation(item)

        staged_state = deepcopy(self._state)
        converted = self._convert(item, staged_state)
        processed: list[Event] = []
        for event in converted:
            candidate = event if self._processor is None else self._processor(item, event.model_copy(deep=True))
            if candidate is None:
                continue
            validated = self._validate_processor_result(event, candidate)
            processed.append(validated)

        # Visibility decisions operate on complete domain events, independently of size.
        framed = [
            frame
            for index, event in enumerate(processed)
            for frame in (
                fragment_custom_event(event, identity=f"{item.thread_id}:{item.run_id}:{item.sequence}:{index}")
                if isinstance(event, CustomEvent)
                else [event]
            )
        ]
        stored = _copy_events(framed)
        self._thread_id = item.thread_id
        self._run_id = item.run_id
        self._state = staged_state
        self._events.extend(stored)
        return _copy_events(stored)

    @property
    def event_count(self) -> int:
        """Number of accumulated frames, usable as a finite snapshot boundary."""
        return len(self._events)

    def snapshot(self, *, start: int = 0, stop: int | None = None) -> tuple[Event, ...]:
        """Return detached accumulated frames in the requested half-open range.

        Capture ``event_count`` once and use it as ``stop`` when reading a
        growing observer in batches. Positions are observer-local, not transport
        sequence numbers. The no-argument form retains the complete snapshot.
        """
        end = len(self._events) if stop is None else stop
        if start < 0 or end < start or end > len(self._events):
            raise ValueError("snapshot range is outside the accumulated events")
        return _copy_events(self._events[start:end])

    def _validate_correlation(self, item: HarnessStreamEvent[Any]) -> None:
        if self._thread_id is not None and item.thread_id != self._thread_id:
            raise AguiObservationError("Harness Thread correlation changed within one observer")
        if self._run_id is not None and item.run_id != self._run_id:
            raise AguiObservationError("Harness Run correlation changed within one observer")

    def _convert(self, item: HarnessStreamEvent[Any], state: _ObserverState) -> list[Event]:
        if isinstance(item, HarnessRunResultEvent):
            return [self._convert_terminal(item)]

        source = item.event
        events: list[Event]
        if isinstance(source, HarnessExtensionEvent):
            _observe_request_lifecycle(source, state)
            return [_custom_harness_event(item, source)]
        if isinstance(source, ModelInputEvent):
            return _convert_input(item, source.content)
        elif isinstance(source, EnqueuedMessagesEvent):
            # Native delivery is authoritative. Never send its raw messages
            # through the generic serializer: they may contain binary payloads.
            content = [
                content
                for message in source.messages
                if isinstance(message, ModelRequest)
                for content in request_input_content(message)
            ]
            return [
                CustomEvent(
                    timestamp=_timestamp_ms(item),
                    name="a13n.pydantic_ai.enqueued_messages",
                    value=_source_value(item, {"event_kind": source.event_kind, "enqueue_id": source.enqueue_id}),
                ),
                *_convert_input(item, content),
            ]
        elif isinstance(source, DeferredToolResultsEvent):
            # Each resolved value is emitted separately as a readable tool result.
            # The batch lifecycle event must never serialize supplemental media.
            return [
                CustomEvent(
                    timestamp=_timestamp_ms(item),
                    name="a13n.pydantic_ai.deferred_tool_results",
                    value=_source_value(
                        item,
                        {
                            "event_kind": source.event_kind,
                            "call_ids": list(source.results.calls),
                            "approval_ids": list(source.results.approvals),
                        },
                    ),
                )
            ]
        elif isinstance(source, CapabilityEvent):
            # Preserve the native kind and payload, including user-defined capabilities.
            custom = CustomEvent(
                timestamp=_timestamp_ms(item),
                name=source.kind,
                value=_source_value(
                    item,
                    (
                        TypeAdapter(CapabilityEvent) if isinstance(source, UnknownCapabilityEvent) else _ANY_ADAPTER
                    ).dump_python(source, mode="json", by_alias=True, warnings="error"),
                ),
            )
            events = [custom]
        elif isinstance(source, PartStartEvent):
            events = _convert_part_start(item, source, state)
        elif isinstance(source, PartDeltaEvent):
            events = _convert_part_delta(item, source, state)
        elif isinstance(source, PartEndEvent):
            events = _convert_part_end(item, source, state)
        elif isinstance(source, FunctionToolResultEvent | OutputToolResultEvent):
            events = _convert_tool_result(item, source)
        else:
            events = []
        return events or [_custom_pydantic_event(item, source)]

    def _convert_terminal(self, item: HarnessRunResultEvent[Any]) -> Event:
        result = item.result
        timestamp = _timestamp_ms(item)
        usage = _agui_usage(result.usage)
        raw_event: dict[str, Any] = {
            "thread_id": item.thread_id,
            "run_id": item.run_id,
            "sequence": item.sequence,
            "occurred_at": item.occurred_at.isoformat(),
            "status": result.status,
        }
        if result.status == "completed":
            output, omitted = _json_safe_output(result.output)
            if omitted:
                raw_event["result_omitted"] = True
            return RunFinishedEvent(
                timestamp=timestamp,
                raw_event=raw_event,
                thread_id=item.thread_id,
                run_id=item.run_id,
                result=output,
                outcome=RunFinishedSuccessOutcome(),
                usage=usage,
            )
        if result.status == "suspended":
            deferred = result.deferred
            assert deferred is not None
            return CustomEvent(
                timestamp=timestamp,
                name="a13n.harness.run_result",
                value=_source_value(
                    item,
                    {
                        "status": "suspended",
                        "suspend_reason": result.suspend_reason,
                        "deferred": _DEFERRED_REQUESTS_ADAPTER.dump_python(
                            deferred,
                            mode="json",
                            by_alias=True,
                        ),
                    },
                ),
            )
        if result.status == "failed":
            failure = result.failure
            assert failure is not None
            raw_event["failure"] = failure.model_dump(mode="json", by_alias=True)
            return RunErrorEvent(
                timestamp=timestamp,
                raw_event=raw_event,
                message=failure.message,
                code=failure.code,
                usage=usage,
            )
        return RunErrorEvent(
            timestamp=timestamp,
            raw_event=raw_event,
            message="The run was cancelled.",
            code="run_cancelled",
            usage=usage,
        )

    def _validate_processor_result(self, original: Event, candidate: object) -> Event:
        if not isinstance(candidate, BaseEvent):
            raise AguiObservationError("The event processor returned a non-AG-UI value")
        try:
            replacement = _AGUI_EVENT_ADAPTER.validate_python(candidate, strict=True)
        except ValueError as exc:
            raise AguiObservationError("The event processor returned an invalid AG-UI event") from exc
        if replacement.type != original.type:
            raise AguiObservationError("The event processor changed the AG-UI event type")

        original_data = original.model_dump(mode="python")
        replacement_data = replacement.model_dump(mode="python")
        mutable_fields = _MUTABLE_FIELDS_BY_EVENT_TYPE.get(original.type, frozenset())
        for field_name, original_value in original_data.items():
            if field_name not in mutable_fields and replacement_data.get(field_name) != original_value:
                raise AguiObservationError(f"The event processor changed structural field {field_name}")
        _validate_nested_source_correlation(original, replacement)
        return replacement.model_copy(deep=True)


def _convert_input(item: HarnessEvent, content: Sequence[UserContent | ContentItem]) -> list[Event]:
    events: list[Event] = []
    for index, native in enumerate(content):
        projected = project_input_content(native)
        if projected is None:
            continue
        value, metadata = projected
        message_id = f"{item.run_id}:input:{item.sequence}:{index}"
        if isinstance(value, str):
            events.extend(
                _text_message_events(item, message_id=message_id, content=value, role="user", metadata=metadata)
            )
        else:
            events.append(
                CustomEvent.model_validate(
                    {
                        "type": "CUSTOM",
                        "timestamp": _timestamp_ms(item),
                        "name": "a13n.input.media",
                        "message_id": message_id,
                        "role": "user",
                        "metadata": metadata.model_dump(mode="json"),
                        "value": _source_value(item, {"content": value}),
                    }
                )
            )
    return events


def _text_message_events(
    item: HarnessEvent, *, message_id: str, content: str, role: Literal["user", "assistant"], metadata: ContentMetadata
) -> list[Event]:
    # Every chunk is independently attributable and below typical transport limits,
    # even when JSON escaping expands a code point to six bytes.
    fields = {
        "message_id": message_id,
        "timestamp": _timestamp_ms(item),
        "role": role,
        "metadata": metadata.model_dump(mode="json"),
    }
    return [
        TextMessageStartEvent.model_validate(fields),
        *(
            TextMessageContentEvent.model_validate({**fields, "delta": content[offset : offset + 8192]})
            for offset in range(0, len(content), 8192)
        ),
        TextMessageEndEvent.model_validate(fields),
    ]


def _convert_part_start(item: HarnessEvent, event: PartStartEvent, state: _ObserverState) -> list[Event]:
    timestamp = _timestamp_ms(item)
    part = event.part
    if isinstance(part, TextPart):
        message_id = part.id or _part_id(item.run_id, state.request_index, event.index, "text")
        cursor = _PartCursor(kind="text", part_id=message_id, emitted_content=bool(part.content))
        state.parts[event.index] = cursor
        events: list[Event] = [TextMessageStartEvent(timestamp=timestamp, message_id=message_id, role="assistant")]
        if part.content:
            events.append(TextMessageContentEvent(timestamp=timestamp, message_id=message_id, delta=part.content))
        return events
    if isinstance(part, ThinkingPart):
        message_id = part.id or _part_id(item.run_id, state.request_index, event.index, "reasoning")
        cursor = _PartCursor(
            kind="reasoning",
            part_id=message_id,
            emitted_content=bool(part.content),
            emitted_signature=bool(part.signature),
        )
        state.parts[event.index] = cursor
        events = [ReasoningMessageStartEvent(timestamp=timestamp, message_id=message_id, role="reasoning")]
        if part.content:
            events.append(ReasoningMessageContentEvent(timestamp=timestamp, message_id=message_id, delta=part.content))
        if part.signature:
            events.append(
                ReasoningEncryptedValueEvent(
                    timestamp=timestamp,
                    subtype="message",
                    entity_id=message_id,
                    encrypted_value=part.signature,
                )
            )
        return events
    if isinstance(part, ToolCallPart):
        state.parts[event.index] = _PartCursor(
            kind="tool_call",
            part_id=part.tool_call_id,
            tool_name=part.tool_name,
        )
        return []
    return []


def _convert_part_delta(item: HarnessEvent, event: PartDeltaEvent, state: _ObserverState) -> list[Event]:
    timestamp = _timestamp_ms(item)
    delta = event.delta
    if isinstance(delta, TextPartDelta):
        cursor, opened = _ensure_part_cursor(item, state, event.index, "text")
        events: list[Event] = []
        if opened:
            events.append(TextMessageStartEvent(timestamp=timestamp, message_id=cursor.part_id, role="assistant"))
        events.append(
            TextMessageContentEvent(timestamp=timestamp, message_id=cursor.part_id, delta=delta.content_delta)
        )
        cursor.emitted_content = True
        return events
    if isinstance(delta, ThinkingPartDelta):
        cursor, opened = _ensure_part_cursor(item, state, event.index, "reasoning")
        events = []
        if opened:
            events.append(ReasoningMessageStartEvent(timestamp=timestamp, message_id=cursor.part_id, role="reasoning"))
        if delta.content_delta:
            events.append(
                ReasoningMessageContentEvent(
                    timestamp=timestamp,
                    message_id=cursor.part_id,
                    delta=delta.content_delta,
                )
            )
            cursor.emitted_content = True
        if delta.signature_delta:
            events.append(
                ReasoningEncryptedValueEvent(
                    timestamp=timestamp,
                    subtype="message",
                    entity_id=cursor.part_id,
                    encrypted_value=delta.signature_delta,
                )
            )
            cursor.emitted_signature = True
        return events
    if isinstance(delta, ToolCallPartDelta):
        cursor, _ = _ensure_tool_cursor(item, state, event.index, delta)
        if delta.tool_name_delta:
            cursor.tool_name = f"{cursor.tool_name or ''}{delta.tool_name_delta}"
        return []
    return []


def _convert_part_end(item: HarnessEvent, event: PartEndEvent, state: _ObserverState) -> list[Event]:
    timestamp = _timestamp_ms(item)
    part = event.part
    existing = state.parts.pop(event.index, None)
    if isinstance(part, TextPart):
        message_id = part.id or (existing.part_id if existing is not None else None)
        message_id = message_id or _part_id(item.run_id, state.request_index, event.index, "text")
        cursor, opened = _ending_cursor(existing, "text", message_id)
        events: list[Event] = []
        if opened:
            events.append(TextMessageStartEvent(timestamp=timestamp, message_id=message_id, role="assistant"))
        if part.content and not cursor.emitted_content:
            events.append(TextMessageContentEvent(timestamp=timestamp, message_id=message_id, delta=part.content))
        events.append(TextMessageEndEvent(timestamp=timestamp, message_id=message_id))
        return events
    if isinstance(part, ThinkingPart):
        message_id = part.id or (existing.part_id if existing is not None else None)
        message_id = message_id or _part_id(item.run_id, state.request_index, event.index, "reasoning")
        cursor, opened = _ending_cursor(existing, "reasoning", message_id)
        events = []
        if opened:
            events.append(ReasoningMessageStartEvent(timestamp=timestamp, message_id=message_id, role="reasoning"))
        if part.content and not cursor.emitted_content:
            events.append(ReasoningMessageContentEvent(timestamp=timestamp, message_id=message_id, delta=part.content))
        if part.signature and not cursor.emitted_signature:
            events.append(
                ReasoningEncryptedValueEvent(
                    timestamp=timestamp,
                    subtype="message",
                    entity_id=message_id,
                    encrypted_value=part.signature,
                )
            )
        events.append(ReasoningMessageEndEvent(timestamp=timestamp, message_id=message_id))
        return events
    if isinstance(part, ToolCallPart):
        tool_call_id = existing.part_id if existing is not None else part.tool_call_id
        _ending_cursor(existing, "tool_call", tool_call_id)
        events = [
            ToolCallStartEvent(
                timestamp=timestamp,
                tool_call_id=tool_call_id,
                tool_call_name=part.tool_name,
            )
        ]
        if part.args is not None:
            events.append(
                ToolCallArgsEvent(
                    timestamp=timestamp,
                    tool_call_id=tool_call_id,
                    delta=_tool_args_text(part.args),
                )
            )
        events.append(ToolCallEndEvent(timestamp=timestamp, tool_call_id=tool_call_id))
        return events
    return []


def _convert_tool_result(
    item: HarnessEvent,
    event: FunctionToolResultEvent | OutputToolResultEvent,
) -> list[Event]:
    part = event.part
    if not isinstance(part, ToolReturnPart) or part.outcome != "success":
        # Preserve native failure/retry correlation, but not model-only media.
        projected_part = (
            replace(part, content=tool_execution_value(part.content, part.metadata))
            if isinstance(part, ToolReturnPart)
            else part
        )
        projected = replace(event, part=projected_part)
        if isinstance(projected, FunctionToolResultEvent):
            projected = replace(projected, content=None)
        return [_custom_pydantic_event(item, projected)]
    # Supplemental native tool content can contain binary media. Only the
    # explicitly marked execution value is readable presentation content.
    return [
        ToolCallResultEvent(
            timestamp=_timestamp_ms(item),
            message_id=f"{part.tool_call_id}:result",
            tool_call_id=part.tool_call_id,
            content=_tool_result_text(tool_execution_value(part.content, part.metadata)),
            role="tool",
        )
    ]


def _observe_request_lifecycle(event: HarnessExtensionEvent, state: _ObserverState) -> None:
    payload = event.payload
    if event.kind != "lifecycle" or not isinstance(payload, dict):
        return
    if payload.get("type") != "model_request_started":
        return
    request_index = payload.get("request_index")
    if isinstance(request_index, int) and request_index >= 0:
        state.request_index = request_index
        # Steering can announce the next request before the previous stream emits PartEnd.
        # Open parts retain their original identity until their own end or replacement start.


def _custom_harness_event(item: HarnessEvent, event: HarnessExtensionEvent) -> CustomEvent:
    name = f"a13n.harness.{event.kind}"
    if event.kind == "tool":
        try:
            tool_event = ToolExtraEventPayload.model_validate(event.payload)
        except ValidationError:
            pass
        else:
            name = f"{name}.{tool_event.name}"
    return CustomEvent(
        timestamp=_timestamp_ms(item),
        name=name,
        value=_source_value(item, event.model_dump(mode="json", by_alias=True)),
    )


def _custom_pydantic_event(item: HarnessEvent, event: AgentStreamEventProtocol) -> CustomEvent:
    source = _ANY_ADAPTER.dump_python(event, mode="json", by_alias=True, warnings="error")
    return CustomEvent(
        timestamp=_timestamp_ms(item),
        name=f"a13n.pydantic_ai.{event.event_kind}",
        value=_source_value(item, source),
    )


def _source_value(item: HarnessStreamEvent[Any], event: object) -> dict[str, object]:
    return {
        "thread_id": item.thread_id,
        "run_id": item.run_id,
        "sequence": item.sequence,
        "occurred_at": item.occurred_at.isoformat(),
        "event": event,
    }


def _ensure_part_cursor(
    item: HarnessEvent,
    state: _ObserverState,
    index: int,
    kind: Literal["text", "reasoning"],
) -> tuple[_PartCursor, bool]:
    existing = state.parts.get(index)
    if existing is not None:
        if existing.kind != kind:
            raise AguiObservationError("Pydantic part kind changed before its end event")
        return existing, False
    cursor = _PartCursor(
        kind=kind,
        part_id=_part_id(item.run_id, state.request_index, index, kind),
    )
    state.parts[index] = cursor
    return cursor, True


def _ensure_tool_cursor(
    item: HarnessEvent,
    state: _ObserverState,
    index: int,
    delta: ToolCallPartDelta,
) -> tuple[_PartCursor, bool]:
    existing = state.parts.get(index)
    if existing is not None:
        if existing.kind != "tool_call":
            raise AguiObservationError("Pydantic part kind changed before its end event")
        if delta.tool_call_id and delta.tool_call_id != existing.part_id:
            raise AguiObservationError("Pydantic tool-call identity changed before its end event")
        return existing, False
    cursor = _PartCursor(
        kind="tool_call",
        part_id=delta.tool_call_id or _part_id(item.run_id, state.request_index, index, "tool"),
        tool_name=None,
    )
    state.parts[index] = cursor
    return cursor, True


def _ending_cursor(
    existing: _PartCursor | None,
    expected_kind: Literal["text", "reasoning", "tool_call"],
    part_id: str,
) -> tuple[_PartCursor, bool]:
    if existing is None:
        return _PartCursor(kind=expected_kind, part_id=part_id), True
    if existing.kind != expected_kind:
        raise AguiObservationError("Pydantic part kind changed before its end event")
    if existing.part_id != part_id:
        raise AguiObservationError("Pydantic part identity changed before its end event")
    return existing, False


def _part_id(run_id: str, request_index: int, part_index: int, kind: str) -> str:
    return f"{run_id}:request-{request_index}:part-{part_index}:{kind}"


def _tool_args_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    serialized = _ANY_ADAPTER.dump_python(value, mode="json")
    return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_safe_output(value: object) -> tuple[JsonValue, bool]:
    try:
        serialized = _ANY_ADAPTER.dump_python(value, mode="json", warnings="error")
        return _JSON_VALUE_ADAPTER.validate_python(serialized, strict=True), False
    except (TypeError, ValueError):
        return None, True


def _tool_result_text(value: object) -> str:
    if isinstance(value, str):
        return value
    serialized = _ANY_ADAPTER.dump_python(value, mode="json")
    return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _agui_usage(usage: Any) -> list[TokenUsage] | None:
    total_tokens = usage.input_tokens + usage.output_tokens
    if total_tokens == 0 and usage.cache_read_tokens == 0:
        return None
    reasoning_tokens = usage.details.get("reasoning_tokens")
    return [
        TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens if isinstance(reasoning_tokens, int) else None,
            cached_input_tokens=usage.cache_read_tokens,
        )
    ]


def _timestamp_ms(item: HarnessStreamEvent[Any]) -> int:
    return int(item.occurred_at.timestamp() * 1000)


def _validate_nested_source_correlation(original: Event, replacement: Event) -> None:
    original_payload: object | None = None
    replacement_payload: object | None = None
    if isinstance(original, CustomEvent) and isinstance(replacement, CustomEvent):
        original_payload = original.value
        replacement_payload = replacement.value
    elif original.raw_event is not None:
        original_payload = original.raw_event
        replacement_payload = replacement.raw_event
    if not isinstance(original_payload, dict) or not isinstance(replacement_payload, dict):
        return
    for field_name in _SOURCE_CORRELATION_FIELDS:
        if field_name in original_payload and replacement_payload.get(field_name) != original_payload[field_name]:
            raise AguiObservationError(f"The event processor changed {field_name} source correlation")


def _copy_events(events: Sequence[Event]) -> tuple[Event, ...]:
    return tuple(cast(Event, event.model_copy(deep=True)) for event in events)


__all__ = ["AguiEventProcessor", "AguiObservationError", "HarnessAguiObserver"]
