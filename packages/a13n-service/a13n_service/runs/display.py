"""What viewers see of a run: ordered display items folded from Harness events, never from message history.

Items keep the shape the Console renders: `{id, ordinal, kind, state, first_stream_id, last_stream_id, started_at,
ended_at, content}`. Stream IDs are `"{attempt}-{sequence}"` positions, so a committed item and a live delta of the same
item order and deduplicate by comparing positions. The worker folds every AG-UI event it streams, so the display
written at a checkpoint covers exactly the stream up to that checkpoint's position. Each Harness observation is one
item, except that the consecutive argument deltas of one streamed tool-call part share one.

The display is written as immutable history pages of final items and a tail of the items after them. An item is
final once no later event of the run can change it: at a checkpoint only the unanswered tool calls of the primary
run's latest model response can still change, so every other unfinished item is interrupted, and full pages are cut
from the front of the tail.
"""

import copy
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from a13n_harness import HarnessEvent, HarnessStreamEvent
from a13n_harness.tools._output import tool_execution_value
from a13n_stream_protocol import AUTHORED_INPUT_EVENT_NAMES, HarnessAguiStreamObserver, tool_result_content
from a13n_stream_protocol.fragments import CustomEventAssembler
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


class ItemRef(BaseModel):
    """The item a live event changed, and its state after the event."""

    id: str
    kind: ItemKind
    state: ItemState


class Tail(BaseModel):
    """The items of a run's display not in a page at one checkpoint, and the stream position the display covers."""

    model_config = ConfigDict(extra="forbid")
    # The ordinal of the first item, or of the next one when there is none: every earlier item is in a page.
    first: int = Field(default=1, ge=1)
    items: list[Item] = Field(default_factory=list)
    position: StreamPosition = StreamPosition(attempt=0, sequence=0)
    # Optional Redis resume hint; attempts without confirmed writes have none.
    resume_after: str | None = None


class Page(BaseModel):
    """Consecutive final items of a run's display, written once."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What one checkpoint writes of the display: the pages it filled and the tail after them."""

    pages: list[Page]
    tail: Tail


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
    """A run's display tail in memory during one attempt, continuing the committed tail it restored. A page holds
    `page_items` items, or fewer that reach `page_bytes`."""

    def __init__(self, run_id: str, tail: Tail, *, attempt: int, page_items: int, page_bytes: int):
        self.run_id, self.attempt = run_id, attempt
        self.page_items, self.page_bytes = page_items, page_bytes
        self.first = tail.first
        self.items = {item.id: item for item in tail.items}
        self.next = tail.first + len(tail.items)
        # The items this attempt moved to pages, which no later event may change.
        self.paged: set[str] = set()
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

    def snapshot(self, open_calls: Collection[str] = ()) -> Snapshot:
        """The display to commit with a state whose open tool calls are `open_calls`: every other unfinished item
        is interrupted, and the final items before the first unfinished one fill as many pages as they can.

        The items stay in the fold until `committed` confirms their pages were. Only a snapshot interrupts items: it
        alone knows which calls the state leaves open, and a takeover continues those in place.
        """
        kept = {item_id(self.run_id, "tool_call", call) for call in open_calls}
        for key, item in self.items.items():
            if item.state == "in_progress" and key not in kept:
                self.items[key] = item.model_copy(update={"state": "interrupted"})
                self.changed.add(key)
        for key in self.changed & self.items.keys():
            self.sizes[key] = _size(self.items[key])
        self.changed.clear()
        items = list(self.items.values())
        pages: list[Page] = []
        start, size = 0, 0
        for end, item in enumerate(items, 1):
            if item.state == "in_progress":
                break
            size += self.sizes[item.id]
            if end - start == self.page_items or size >= self.page_bytes:
                pages.append(Page(items=items[start:end]))
                start, size = end, 0
        return Snapshot(pages, Tail(first=self.first + start, items=items[start:], position=self.position))

    def committed(self, snapshot: Snapshot) -> None:
        """The snapshot's pages were committed: their items leave the tail for good."""
        for page in snapshot.pages:
            for item in page.items:
                del self.items[item.id], self.sizes[item.id]
                self.paged.add(item.id)
        self.first = snapshot.tail.first

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
        if event_type in _ENDS:
            state: ItemState = "completed"
        else:
            state = "in_progress" if previous is None else previous.state
        return self._put(key, kind, state, content, at=_occurred(payload))

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
        if key in self.paged:
            raise RuntimeError("An event changed a display item that is already in a page")
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
        return ItemRef(id=key, kind=kind, state=state)
