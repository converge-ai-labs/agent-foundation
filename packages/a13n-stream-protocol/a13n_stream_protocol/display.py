"""Shared semantic display snapshots and raw-event normalization; Hosts own persistence and paging."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from a13n_harness import HarnessEvent, HarnessStreamEvent
from a13n_harness.tools._output import tool_execution_value
from ag_ui.core import Event, TextPart, ToolCallArgsEvent, ToolCallResultEvent
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessage,
    ModelResponse,
    OutputToolResultEvent,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from a13n_stream_protocol import AUTHORED_INPUT_EVENT_NAMES, HarnessAguiStreamObserver, tool_result_content
from a13n_stream_protocol.fragments import CustomEventAssembler, FragmentState
from a13n_stream_protocol.observer import AguiEventProcessor, ObserverContinuation

type ItemKind = Literal["text_message", "reasoning_message", "tool_call", "observation"]
type ItemState = Literal["in_progress", "completed", "interrupted", "failed"]

# Harness observations beyond this size keep only their name: Debug evidence, not an unbounded payload copy.
MAX_OBSERVATION_BYTES = 32768
# A message text, tool arguments or tool result keeps this many characters; the full value stays in the state.
MAX_FIELD_CHARS = 262144

_MESSAGE_KINDS: dict[str, ItemKind] = {
    "TEXT_MESSAGE_START": "text_message",
    "TEXT_MESSAGE_CONTENT": "text_message",
    "TEXT_MESSAGE_END": "text_message",
    "REASONING_MESSAGE_START": "reasoning_message",
    "REASONING_MESSAGE_CONTENT": "reasoning_message",
    "REASONING_MESSAGE_END": "reasoning_message",
    "REASONING_ENCRYPTED_VALUE": "reasoning_message",
}
_TOOL_EVENTS = frozenset({"TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"})
_ENDS = frozenset({"TEXT_MESSAGE_END", "REASONING_MESSAGE_END", "TOOL_CALL_RESULT"})
_FINISHED: frozenset[ItemState] = frozenset({"completed", "failed"})
_COPIED = (
    "messageId",
    "role",
    "toolCallId",
    "toolCallName",
    "parentMessageId",
    "metadata",
    "subagentRunId",
    "responseGroup",
)
_ACCUMULATED = {
    "TEXT_MESSAGE_CONTENT": "text",
    "REASONING_MESSAGE_CONTENT": "text",
    "TOOL_CALL_ARGS": "arguments",
}
# Events that append their `delta` to one message or tool call.
FRAGMENTS = frozenset(_ACCUMULATED)
# The observation the observer reports for each streamed delta of a model response part, a tool call's arguments
# included: the stream protocol completes a tool call only at the part's end.
_PART_DELTA = "a13n.pydantic_ai.part_delta"


class StreamPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt: int = Field(ge=0)
    sequence: int = Field(ge=0)

    def __str__(self) -> str:
        return f"{self.attempt}-{self.sequence}"


class ContentRef(BaseModel):
    """An immutable Host-owned value, loaded through that Host's authorized content API."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    size_bytes: int = Field(ge=0)
    media_type: Literal["text/plain", "application/json"]
    preview: str
    truncated: bool = False


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    # The item's place in the run's display: 1 for the first item, dense after it.
    ordinal: int = Field(ge=1)
    kind: ItemKind
    state: ItemState
    first_stream_id: str
    last_stream_id: str
    # When the item's first event occurred, and the latest event that left it completed or failed.
    started_at: datetime
    ended_at: datetime | None = None
    content: dict[str, JsonValue]
    content_refs: dict[str, ContentRef] = Field(default_factory=dict, exclude_if=lambda value: not value)


class ItemRef(BaseModel):
    """The item a live event changed, and its state after the event."""

    id: str
    kind: ItemKind
    state: ItemState
    ordinal: int | None = Field(default=None, ge=1)
    # Host-selected grouping and native failure diagnostics are not text deltas.
    response_group: str | None = None
    failure: dict[str, JsonValue] | None = None


class Observed(BaseModel):
    """One AG-UI event at its stream sequence, with the item it changed."""

    sequence: int
    event: dict[str, Any]
    item: ItemRef | None


_OMITTED: dict[str, JsonValue] = {"omitted": True}


def _bounded(content: dict[str, JsonValue], field: str, value: str) -> None:
    content[field] = value[:MAX_FIELD_CHARS]
    if len(value) > MAX_FIELD_CHARS:
        content["truncated"] = True


def _json_size(value: JsonValue) -> int:
    return len(json.dumps(value, separators=(",", ":")))


def _streamed_arguments(event: dict[str, Any]) -> dict[str, Any] | None:
    """The source delta of a streamed tool-call argument observation; None for any other event."""
    if event["type"] != "CUSTOM" or event.get("name") != _PART_DELTA:
        return None
    delta = event["value"]["event"]["delta"]
    arguments = delta.get("part_delta_kind") == "tool_call" and isinstance(delta.get("args_delta"), str)
    return delta if arguments and not delta.get("tool_name_delta") else None


def fragment(event: dict[str, Any]) -> tuple[JsonValue, str] | None:
    """The stream a fragment event continues and the text it appends; None for any other event.

    Text, reasoning and tool-call argument deltas continue their message or tool call, and a streamed tool-call
    argument observation continues its model response part. Fragments of one stream differ only in their text and in
    when, and at which source position, they occurred.
    """
    if event["type"] in FRAGMENTS:
        return {name: value for name, value in event.items() if name not in ("delta", "timestamp")}, event["delta"]
    if (delta := _streamed_arguments(event)) is None:
        return None
    value = event["value"]
    source = {**value["event"], "delta": {**delta, "args_delta": None}}
    return [event["name"], value["thread_id"], value["run_id"], source], delta["args_delta"]


def extend(event: dict[str, Any], text: str) -> None:
    """Append a later fragment's text to a fragment event."""
    if event["type"] == "CUSTOM":
        event["value"]["event"]["delta"]["args_delta"] += text
    else:
        event["delta"] += text


@dataclass
class _Arguments:
    """A tool-call part's streamed arguments so far and the one observation item that holds them."""

    stream: JsonValue
    key: str
    at: datetime
    # The sequence of the last delta folded in: only the next event continues the item.
    sequence: int
    # The whole observation until its value outgrows the observation limit.
    event: dict[str, Any] | None
    size: int


class DisplayContinuation(BaseModel):
    """Parsing state at a semantic cut; Host paging may retire only immutable items."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    position: StreamPosition
    next_ordinal: int = Field(ge=1)
    full_content: bool = False
    response_groups: dict[str, str] = Field(default_factory=dict)
    arguments: _Arguments | None = None
    fragments: FragmentState = Field(default_factory=FragmentState)
    observer: ObserverContinuation = Field(default_factory=ObserverContinuation)


class DisplaySnapshot(BaseModel):
    """Completed blocks and active accumulations, with enough state for an intact suffix."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item]
    continuation: DisplayContinuation


def item_id(run_id: str, kind: ItemKind, source_id: str) -> str:
    """Stable across attempts: a tool call retried after recovery updates the item already committed."""
    return "itm_" + hashlib.sha256(f"{run_id}\0{kind}\0{source_id}".encode()).hexdigest()[:32]


def open_tool_calls(messages: Sequence[ModelMessage]) -> frozenset[str]:
    """The tool calls of the latest model response that no later request answers."""
    answered: set[str] = set()
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            calls = (part.tool_call_id for part in message.parts if isinstance(part, ToolCallPart))
            return frozenset(call for call in calls if call not in answered)
        answered.update(
            part.tool_call_id for part in message.parts if isinstance(part, ToolReturnPart | RetryPromptPart)
        )
    return frozenset()


def _occurred(payload: Mapping[str, object]) -> datetime:
    """The event's own time in epoch milliseconds, or the worker's clock for an event that carries none."""
    timestamp = payload.get("timestamp")
    return datetime.fromtimestamp(timestamp / 1000, UTC) if isinstance(timestamp, int) else datetime.now(UTC)


def _failed_tool_call(source: HarnessStreamEvent[Any]) -> tuple[str, str] | None:
    """A tool result the observer does not present (retry prompts, denials): its call ID and message."""
    if not isinstance(source, HarnessEvent):
        return None
    event = source.event
    if not isinstance(event, FunctionToolResultEvent | OutputToolResultEvent):
        return None
    part = event.part
    if isinstance(part, ToolReturnPart) and part.outcome == "success":
        return None
    value = tool_execution_value(part.content, part.metadata) if isinstance(part, ToolReturnPart) else part.content
    content = tool_result_content(value)
    message = (
        content
        if isinstance(content, str)
        else json.dumps([item.model_dump(mode="json", by_alias=True) for item in content])
    )
    return part.tool_call_id, message[:4096]


def bound_event(event: Event) -> Event:
    """Cap the payloads the observer retains for the whole attempt; the state keeps them whole.

    One character over the bound survives, so the fold still sees the value was truncated.
    """
    if (
        isinstance(event, ToolCallResultEvent)
        and isinstance(event.content, str)
        and len(event.content) > MAX_FIELD_CHARS
    ):
        return event.model_copy(update={"content": event.content[: MAX_FIELD_CHARS + 1]})
    if isinstance(event, ToolCallResultEvent) and isinstance(event.content, list):
        if len(event.model_dump_json()) > MAX_FIELD_CHARS:
            return event.model_copy(
                update={"content": [TextPart(text="Result parts omitted: display size limit exceeded.")]}
            )
    if isinstance(event, ToolCallArgsEvent) and len(event.delta) > MAX_FIELD_CHARS:
        return event.model_copy(update={"delta": event.delta[: MAX_FIELD_CHARS + 1]})
    return event


class DisplayFold:
    """Fold converted events once into compact items, independently of Host storage."""

    def __init__(
        self,
        run_id: str,
        items: Sequence[Item] = (),
        *,
        attempt: int = 0,
        first: int = 1,
        full_content: bool = False,
        retain_content: bool = False,
        processor: AguiEventProcessor | None = None,
        continuation: DisplayContinuation | None = None,
    ):
        self.run_id, self.attempt = run_id, attempt
        self.items = {item.id: item.model_copy(deep=True) for item in items}
        self.next = first + len(items)
        self.changed: set[str] = set(self.items)
        self.sequence = 0
        self.full_content = full_content
        # Keep the same item identities and observation policy, but let a durable Host store full values.
        # Unlike full_content, this does not suppress native observations for an interactive Host.
        self.retain_content = retain_content
        self.response_groups: dict[str, str] = {}

        def process(source: HarnessStreamEvent[Any], event: Event) -> Event | None:
            selected = processor(source, event) if processor is not None else event
            return selected if selected is None or full_content or retain_content else bound_event(selected)

        self.observer = HarnessAguiStreamObserver(processor=process, retain_events=False)
        self.assembler = CustomEventAssembler()
        self.arguments: _Arguments | None = None
        if continuation is not None:
            state = continuation.model_copy(deep=True)
            if state.run_id != run_id or state.position.attempt != attempt or state.full_content != full_content:
                raise ValueError("Display continuation does not match the run, attempt or content policy")
            self.next, self.sequence = state.next_ordinal, state.position.sequence
            self.response_groups, self.arguments = state.response_groups, state.arguments
            self.assembler = CustomEventAssembler.restore(state.fragments)
            self.observer = HarnessAguiStreamObserver.restore(state.observer, processor=process)

    def export(self) -> DisplaySnapshot:
        """Detach a pure cut. Export never ends, interrupts or pages active content."""
        return DisplaySnapshot(
            items=list(self.items.values()),
            continuation=self.export_continuation(),
        ).model_copy(deep=True)

    def export_continuation(self) -> DisplayContinuation:
        """Detach parser state without copying the accumulated display prefix."""
        return DisplayContinuation(
            run_id=self.run_id,
            position=self.position,
            next_ordinal=self.next,
            full_content=self.full_content,
            response_groups=self.response_groups,
            arguments=self.arguments,
            fragments=self.assembler.export(),
            observer=self.observer.export(),
        ).model_copy(deep=True)

    @staticmethod
    def restore(snapshot: DisplaySnapshot, *, processor: AguiEventProcessor | None = None) -> DisplayFold:
        state = snapshot.continuation
        return DisplayFold(
            state.run_id,
            snapshot.items,
            attempt=state.position.attempt,
            full_content=state.full_content,
            processor=processor,
            continuation=state,
        )

    def _bounded(self, content: dict[str, JsonValue], field: str, value: str) -> None:
        if self.full_content or self.retain_content:
            content[field] = value
        else:
            _bounded(content, field, value)

    @property
    def position(self) -> StreamPosition:
        return StreamPosition(attempt=self.attempt, sequence=self.sequence)

    def events(self, source: HarnessStreamEvent[Any]) -> list[dict[str, Any]]:
        """The AG-UI events of one Harness event, as the JSON the stream carries."""
        return [event.model_dump(mode="json", by_alias=True) for event in self.observer.observe(source)]

    def fold(self, events: list[dict[str, Any]], source: HarnessStreamEvent[Any] | None = None) -> list[Observed]:
        """Give each event the next sequence and fold it. When `source` is a tool result the observer does not
        present, its call's item fails and the last event reports it."""
        observed: list[Observed] = []
        for payload in events:
            self.sequence += 1
            ref = self._fold(payload)
            observed.append(Observed(sequence=self.sequence, event=payload, item=ref))
        if observed and source is not None and (failed := _failed_tool_call(source)) is not None:
            ref = self._fail_tool_call(
                *failed,
                at=_occurred(observed[-1].event),
                subagent_run_id=source.run_id if source.run_id != self.observer.run_id else None,
            )
            if ref is not None:
                observed[-1] = observed[-1].model_copy(update={"item": ref})
        return observed

    def _fold(self, payload: dict[str, Any]) -> ItemRef | None:
        event_type = payload["type"]
        if event_type == "CUSTOM":
            return self._observation(payload)
        if event_type in {
            "RUN_STARTED",
            "RUN_FINISHED",
            "RUN_ERROR",
            "SUBAGENT_STARTED",
            "SUBAGENT_FINISHED",
            "SUBAGENT_ERROR",
        }:
            key = item_id(self.run_id, "observation", f"{self.attempt}:{self.sequence}")
            return self._put(key, "observation", "completed", payload, at=_occurred(payload))
        if event_type in _MESSAGE_KINDS:
            # An encrypted value names its reasoning message as the entity it belongs to.
            source_id = payload["entityId"] if event_type == "REASONING_ENCRYPTED_VALUE" else payload["messageId"]
            kind = _MESSAGE_KINDS[event_type]
        elif event_type in _TOOL_EVENTS:
            kind, source_id = "tool_call", payload["toolCallId"]
        else:
            return None
        key = item_id(
            self.run_id, kind, f"{payload['subagentRunId']}:{source_id}" if payload.get("subagentRunId") else source_id
        )
        previous = self.items.get(key)
        content: dict[str, JsonValue] = dict(previous.content) if previous is not None else {}
        content.update({name: payload[name] for name in _COPIED if name in payload})
        group = self.response_groups.get(str(content.get("subagentRunId") or "root"))
        if group is not None and "responseGroup" not in content:
            content["responseGroup"] = group
        if event_type == "TOOL_CALL_END":
            content["arguments_complete"] = True
        if (accumulated := _ACCUMULATED.get(event_type)) is not None:
            self._bounded(content, accumulated, str(content.get(accumulated, "")) + payload["delta"])
        if event_type == "REASONING_ENCRYPTED_VALUE":
            content["encrypted_value"] = payload.get("encryptedValue")
        if event_type == "TOOL_CALL_RESULT":
            result = payload.get("content", "")
            if isinstance(result, list):
                content["result_parts"] = (
                    result if self.full_content or self.retain_content or _json_size(result) <= MAX_FIELD_CHARS else []
                )
                if not content["result_parts"]:
                    content["truncated"] = True
            else:
                self._bounded(content, "result", str(result))
        if event_type in _ENDS:
            state: ItemState = "completed"
        else:
            state = "in_progress" if previous is None else previous.state
        return self._put(key, kind, state, content, at=_occurred(payload))

    def _observation(self, payload: dict[str, Any]) -> ItemRef | None:
        if (streamed := fragment(payload)) is not None:
            return None if self.full_content else self._arguments(payload, *streamed)
        assembled = self.assembler.accept(payload)
        if assembled is None:
            return None
        value: JsonValue = assembled.get("value")  # type: ignore[assignment]
        input_event = value.get("event") if isinstance(value, dict) else None
        lifecycle = input_event.get("payload") if isinstance(input_event, dict) else None
        if (
            assembled.get("name") == "a13n.harness.lifecycle"
            and isinstance(lifecycle, dict)
            and lifecycle.get("type") == "model_request_started"
        ):
            scope = str(assembled.get("subagentRunId") or "root")
            self.response_groups[scope] = f"{self.run_id}:{self.attempt}:{self.sequence}"
        media_input = (
            assembled.get("name") == "a13n.input.media"
            and isinstance(input_event, dict)
            and input_event.get("source") in {"user", "steering"}
        )
        if assembled.get("name") in AUTHORED_INPUT_EVENT_NAMES or media_input:
            assert isinstance(value, dict) and isinstance(value["event"], dict)
            message_id = str(value["event"]["message_id"])
            content: dict[str, JsonValue] = {"messageId": message_id, "role": "user"}
            if "subagentRunId" in assembled:
                content["subagentRunId"] = cast(JsonValue, assembled["subagentRunId"])
            if "metadata" in assembled:
                content["metadata"] = cast(JsonValue, assembled["metadata"])
            if media_input:
                content["input_media"] = value["event"]["content"]
            else:
                self._bounded(content, "text", str(value["event"]["content"]))
            content["input_source"] = value["event"].get("source", "user")
            content["input_group"] = value["event"].get("input_id")
            scope = assembled.get("subagentRunId")
            key = item_id(self.run_id, "text_message", f"{scope}:{message_id}" if scope else message_id)
            return self._put(key, "text_message", "completed", content, at=_occurred(assembled))
        if isinstance(input_event, dict) and (ref := self._tool_observation(assembled, input_event)) is not None:
            return ref
        if self.full_content and assembled.get("name") in {
            "a13n.pydantic_ai.part_start",
            "a13n.pydantic_ai.part_end",
            "a13n.pydantic_ai.part_delta",
        }:
            return None
        summary = assembled.get("name") in {"a13n.context.handoff_summary", "a13n.context.compaction_summary"}
        if (
            not self.retain_content
            and not (self.full_content and summary)
            and _json_size(value) > MAX_OBSERVATION_BYTES
        ):
            value = _OMITTED
        key = item_id(self.run_id, "observation", f"{self.attempt}:{self.sequence}")
        content: dict[str, JsonValue] = {"name": str(assembled.get("name")), "value": value}
        if "metadata" in assembled:
            content["metadata"] = cast(JsonValue, assembled["metadata"])
        if "subagentRunId" in assembled:
            content["subagentRunId"] = cast(JsonValue, assembled["subagentRunId"])
        return self._put(key, "observation", "completed", content, at=_occurred(payload))

    def _tool_observation(self, event: Mapping[str, object], value: dict[str, Any]) -> ItemRef | None:
        name, part = event.get("name"), value.get("part")
        native = isinstance(part, dict) and part.get("part_kind") in {"builtin-tool-call", "builtin-tool-return"}
        result = name in {"a13n.pydantic_ai.function_tool_result", "a13n.pydantic_ai.output_tool_result"}
        supplement = name in {"a13n.filesystem.edit_applied", "a13n.harness-ui.tool_images", "a13n.harness-ui.mcp_apps"}
        source = part if native or result else value if supplement else None
        if not isinstance(source, dict) or not isinstance(call_id := source.get("tool_call_id"), str):
            return None
        scope = event.get("subagentRunId")
        provider = str(source.get("provider_name") or "provider") if native else None
        identity = f"native:{provider}:{call_id}" if native else call_id
        key = item_id(self.run_id, "tool_call", f"{scope}:{identity}" if scope else identity)
        previous = self.items.get(key)
        content = dict(previous.content) if previous is not None else {}
        content["toolCallId"] = call_id
        if isinstance(scope, str):
            content["subagentRunId"] = scope
        if provider:
            content["provider"] = provider
        if "tool_name" in source:
            content["toolCallName"] = source["tool_name"]
        group = self.response_groups.get(str(scope or "root"))
        if group is not None:
            content.setdefault("responseGroup", group)
        state: ItemState = "in_progress" if previous is None else previous.state
        if supplement:
            if name == "a13n.filesystem.edit_applied":
                content["applied_edit"] = {key: source.get(key) for key in ("file_path", "before", "after")}
            elif name == "a13n.harness-ui.tool_images":
                content["tool_images"] = source.get("images", [])
                content["tool_image_unavailable"] = source.get("unavailable", False)
            else:
                content["mcp_apps"] = source.get("apps", [])
        elif source.get("part_kind") == "builtin-tool-call":
            args = source.get("args")
            self._bounded(
                content,
                "arguments",
                args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, separators=(",", ":")),
            )
            content["arguments_complete"] = True
        else:
            result_value = source.get("content")
            if self.full_content or self.retain_content or _json_size(result_value) <= MAX_FIELD_CHARS:
                content["value"] = result_value
            else:
                content["truncated"] = True
            content["outcome"] = source.get("outcome")
            content["retry"] = source.get("part_kind") == "retry-prompt"
            state = (
                "failed"
                if content["retry"] or content["outcome"] in {"failed", "denied", "interrupted"}
                else "completed"
            )
        return self._put(key, "tool_call", state, content, at=_occurred(event))

    def _arguments(self, event: dict[str, Any], stream: JsonValue, text: str) -> ItemRef:
        """Consecutive argument deltas of one tool-call part fold into one observation: the first delta's, with the
        argument text of all of them and the first one's time."""
        held = self.arguments
        if held is not None and held.stream == stream and held.sequence == self.sequence - 1:
            if held.event is not None:
                extend(held.event, text)
                # JSON escapes character by character, so the value grows by the escaped text alone.
                held.size += _json_size(text) - 2
        else:
            event = copy.deepcopy(event)
            key = item_id(self.run_id, "observation", f"{self.attempt}:{self.sequence}")
            held = self.arguments = _Arguments(stream, key, _occurred(event), 0, event, _json_size(event["value"]))
        held.sequence = self.sequence
        if not self.retain_content and held.size > MAX_OBSERVATION_BYTES:
            held.event = None
        value = copy.deepcopy(held.event["value"]) if held.event is not None else _OMITTED
        content: dict[str, JsonValue] = {"name": _PART_DELTA, "value": value}
        if "subagentRunId" in event:
            content["subagentRunId"] = cast(JsonValue, event["subagentRunId"])
        return self._put(held.key, "observation", "completed", content, at=held.at)

    def _fail_tool_call(
        self, tool_call_id: str, message: str, *, at: datetime, subagent_run_id: str | None = None
    ) -> ItemRef | None:
        key = item_id(
            self.run_id, "tool_call", f"{subagent_run_id}:{tool_call_id}" if subagent_run_id else tool_call_id
        )
        previous = self.items.get(key)
        if previous is None:
            return None
        content = {**previous.content, "failure": {"code": "tool_failed", "message": message}}
        return self._put(key, "tool_call", "failed", content, at=at)

    def _put(
        self,
        key: str,
        kind: ItemKind,
        state: ItemState,
        content: dict[str, JsonValue],
        *,
        at: datetime,
    ) -> ItemRef:
        position = str(self.position)
        previous = self.items.get(key)
        if previous is None:
            ordinal, self.next = self.next, self.next + 1
        else:
            ordinal = previous.ordinal
        self.items[key] = Item(
            id=key,
            ordinal=ordinal,
            kind=kind,
            state=state,
            first_stream_id=previous.first_stream_id if previous is not None else position,
            last_stream_id=position,
            started_at=previous.started_at if previous is not None else at,
            ended_at=at if state in _FINISHED else None,
            content=content,
        )
        self.changed.add(key)
        group, failure = content.get("responseGroup"), content.get("failure")
        return ItemRef(
            id=key,
            kind=kind,
            state=state,
            ordinal=ordinal,
            response_group=group if isinstance(group, str) else None,
            failure=failure if isinstance(failure, dict) else None,
        )
