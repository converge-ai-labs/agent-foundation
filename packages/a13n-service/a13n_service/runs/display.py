"""What viewers see of a run: ordered display items folded from Harness events, never from message history.

Items keep the shape the Console renders: `{id, kind, state, first_stream_id, last_stream_id, started_at, ended_at,
content}`. Stream IDs are `"{attempt}-{sequence}"` positions, so a committed item and a live delta of the same item order and deduplicate
by comparing positions. The worker folds every AG-UI event it streams, so the display written at a checkpoint
covers exactly the stream up to that checkpoint's position. Each Harness observation is one item, except that the
consecutive argument deltas of one streamed tool-call part share one.
"""

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from a13n_harness import HarnessEvent, HarnessStreamEvent
from a13n_harness.tools._output import tool_execution_value
from a13n_stream_protocol import AUTHORED_INPUT_EVENT_NAMES, HarnessAguiStreamObserver, tool_result_content
from a13n_stream_protocol.fragments import CustomEventAssembler
from ag_ui.core import Event, TextPart, ToolCallArgsEvent, ToolCallResultEvent
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai.messages import FunctionToolResultEvent, OutputToolResultEvent, ToolReturnPart

type ItemKind = Literal["text_message", "reasoning_message", "tool_call", "observation"]
type ItemState = Literal["in_progress", "completed", "interrupted", "failed"]

# Harness observations beyond this size keep only their name: Debug evidence, not an unbounded payload copy.
MAX_OBSERVATION_BYTES = 32768
# A message text, tool arguments or tool result keeps this many characters; the full value stays in the state.
MAX_FIELD_CHARS = 262144
# A display keeps this many items; older ones are dropped and counted, while the state keeps every message.
MAX_ITEMS = 4096

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
_COPIED = ("messageId", "role", "toolCallId", "toolCallName", "parentMessageId", "metadata", "subagentRunId")
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


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: ItemKind
    state: ItemState
    first_stream_id: str
    last_stream_id: str
    # When the item's first event occurred, and the latest event that left it completed or failed.
    started_at: datetime
    ended_at: datetime | None = None
    content: dict[str, JsonValue]


class ItemRef(BaseModel):
    """The item a live event changed, and its state after the event."""

    id: str
    kind: ItemKind
    state: ItemState


class Display(BaseModel):
    """The display of a run at one checkpoint and the stream position it covers."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(default_factory=list)
    position: StreamPosition = StreamPosition(attempt=0, sequence=0)
    # Optional Redis resume hint; older displays and attempts without confirmed writes have none.
    resume_after: str | None = None
    # Items dropped from the front over the item limit.
    dropped: int = Field(default=0, ge=0)


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


def _size(item: Item) -> int:
    return len(item.model_dump_json().encode("utf-8"))


def _json_size(value: JsonValue) -> int:
    return len(json.dumps(value, separators=(",", ":")))


def _streamed_arguments(event: dict[str, Any]) -> dict[str, Any] | None:
    """The source delta of a streamed tool-call argument observation; None for any other event."""
    if event["type"] != "CUSTOM" or event.get("name") != _PART_DELTA:
        return None
    delta = event["value"]["event"]["delta"]
    arguments = delta.get("part_delta_kind") == "tool_call" and isinstance(delta.get("args_delta"), str)
    return delta if arguments and not delta.get("tool_name_delta") else None


def fragment(event: dict[str, Any]) -> tuple[object, str] | None:
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
    return (event["name"], value["thread_id"], value["run_id"], source), delta["args_delta"]


def extend(event: dict[str, Any], text: str) -> None:
    """Append a later fragment's text to a fragment event."""
    if event["type"] == "CUSTOM":
        event["value"]["event"]["delta"]["args_delta"] += text
    else:
        event["delta"] += text


@dataclass
class _Arguments:
    """A tool-call part's streamed arguments so far and the one observation item that holds them."""

    stream: object
    key: str
    at: datetime
    # The sequence of the last delta folded in: only the next event continues the item.
    sequence: int
    # The whole observation until its value outgrows the observation limit.
    event: dict[str, Any] | None
    size: int


def item_id(run_id: str, kind: ItemKind, source_id: str) -> str:
    """Stable across attempts: a tool call retried after recovery updates the item already committed."""
    return "itm_" + hashlib.sha256(f"{run_id}\0{kind}\0{source_id}".encode()).hexdigest()[:32]


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


def _bound_payloads(source: HarnessStreamEvent[Any], event: Event) -> Event:
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
    """A run's display in memory during one attempt, continuing the committed display it restored."""

    def __init__(self, run_id: str, display: Display, *, attempt: int, max_bytes: int):
        self.run_id, self.attempt, self.max_bytes = run_id, attempt, max_bytes
        self.items = {item.id: item for item in display.items}
        self.dropped = display.dropped
        # Each item's serialized size as of the last snapshot; a snapshot measures only the items changed since.
        self.sizes: dict[str, int] = {}
        self.changed: set[str] = set(self.items)
        self.sequence = 0
        self.observer = HarnessAguiStreamObserver(processor=_bound_payloads)
        # Assembly precedes display truncation; a small saved-display budget
        # must not erase the existence of a valid fragmented input message.
        self.assembler = CustomEventAssembler()
        self.arguments: _Arguments | None = None

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
            observed.append(Observed(sequence=self.sequence, event=payload, item=self._fold(payload)))
        if observed and source is not None and (failed := _failed_tool_call(source)) is not None:
            ref = self._fail_tool_call(
                *failed,
                at=_occurred(observed[-1].event),
                subagent_run_id=source.run_id if source.run_id != self.observer.run_id else None,
            )
            if ref is not None:
                observed[-1] = observed[-1].model_copy(update={"item": ref})
        return observed

    def snapshot(self) -> Display:
        """The display to commit. Over its item limit the oldest items are dropped; over its byte limit the oldest
        remaining ones give up their content.

        The display is a view, so its limits never fail the run: the state still holds every message.
        """
        for key in self.changed & self.items.keys():
            self.sizes[key] = _size(self.items[key])
        self.changed.clear()
        for key in list(self.items)[: max(0, len(self.items) - MAX_ITEMS)]:
            del self.items[key], self.sizes[key]
            self.dropped += 1
        excess = sum(self.sizes.values()) - self.max_bytes
        for key, item in self.items.items():
            if excess <= 0:
                break
            if item.content != _OMITTED:
                omitted = item.model_copy(update={"content": _OMITTED})
                excess -= self.sizes[key] - (size := _size(omitted))
                self.items[key], self.sizes[key] = omitted, size
        return Display(items=list(self.items.values()), position=self.position, dropped=self.dropped)

    def interrupt(self) -> None:
        """Unfinished items of an attempt that ends without finishing them."""
        for key, item in self.items.items():
            if item.state == "in_progress":
                self.items[key] = item.model_copy(update={"state": "interrupted"})
                self.changed.add(key)

    def _fold(self, payload: dict[str, Any]) -> ItemRef | None:
        event_type = payload["type"]
        if event_type == "CUSTOM":
            return self._observation(payload)
        if event_type == "RUN_FINISHED" and payload.get("outcome", {}).get("type") == "interrupt":
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
        if (accumulated := _ACCUMULATED.get(event_type)) is not None:
            _bounded(content, accumulated, str(content.get(accumulated, "")) + payload["delta"])
        if event_type == "REASONING_ENCRYPTED_VALUE":
            content["encrypted_value"] = payload.get("encryptedValue")
        if event_type == "TOOL_CALL_RESULT":
            result = payload.get("content", "")
            if isinstance(result, list):
                content["result_parts"] = result if _json_size(result) <= MAX_FIELD_CHARS else []
                if not content["result_parts"]:
                    content["truncated"] = True
            else:
                _bounded(content, "result", str(result))
        state: ItemState = "completed" if event_type in _ENDS else self._continued(previous)
        return self._put(key, kind, state, content, at=_occurred(payload))

    @staticmethod
    def _continued(previous: Item | None) -> ItemState:
        """A later event keeps a finished item's state; one an earlier attempt left interrupted resumes."""
        return "in_progress" if previous is None or previous.state == "interrupted" else previous.state

    def _observation(self, payload: dict[str, Any]) -> ItemRef | None:
        if (streamed := fragment(payload)) is not None:
            return self._arguments(payload, *streamed)
        assembled = self.assembler.accept(payload)
        if assembled is None:
            return None
        value: JsonValue = assembled.get("value")  # type: ignore[assignment]
        if assembled.get("name") in AUTHORED_INPUT_EVENT_NAMES:
            assert isinstance(value, dict) and isinstance(value["event"], dict)
            message_id = str(value["event"]["message_id"])
            content: dict[str, JsonValue] = {"messageId": message_id, "role": "user"}
            if "subagentRunId" in assembled:
                content["subagentRunId"] = cast(JsonValue, assembled["subagentRunId"])
            if "metadata" in assembled:
                content["metadata"] = cast(JsonValue, assembled["metadata"])
            _bounded(content, "text", str(value["event"]["content"]))
            key = item_id(self.run_id, "text_message", message_id)
            return self._put(key, "text_message", "completed", content, at=_occurred(assembled))
        if _json_size(value) > MAX_OBSERVATION_BYTES:
            value = _OMITTED
        key = item_id(self.run_id, "observation", f"{self.attempt}:{self.sequence}")
        content: dict[str, JsonValue] = {"name": str(assembled.get("name")), "value": value}
        if "subagentRunId" in assembled:
            content["subagentRunId"] = cast(JsonValue, assembled["subagentRunId"])
        return self._put(key, "observation", "completed", content, at=_occurred(payload))

    def _arguments(self, event: dict[str, Any], stream: object, text: str) -> ItemRef:
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
        if held.size > MAX_OBSERVATION_BYTES:
            held.event = None
        value = held.event["value"] if held.event is not None else _OMITTED
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
        self.items[key] = Item(
            id=key,
            kind=kind,
            state=state,
            first_stream_id=previous.first_stream_id if previous is not None else position,
            last_stream_id=position,
            started_at=previous.started_at if previous is not None else at,
            ended_at=at if state in _FINISHED else None,
            content=content,
        )
        self.changed.add(key)
        return ItemRef(id=key, kind=kind, state=state)
