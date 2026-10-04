"""UI-owned compact display snapshots, independent of replaceable model context."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from a13n_harness import HarnessStreamEvent
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, HarnessState, decode_messages
from a13n_stream_protocol.display import DisplayFold, Item, ItemChange, StreamPosition, item_change
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

_STATE_KEY = "a13n.harness-ui.display-history"
_COMPLETED_KEY = "a13n.harness-ui.completed"


class DisplayHistory(BaseModel):
    """A detached compact prefix selected with one native execution checkpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str | None = None
    items: tuple[Item, ...] = ()
    position: StreamPosition = Field(default_factory=lambda: StreamPosition(attempt=0, sequence=0))
    completed: tuple[str, ...] = ()
    pending_response: str | None = None


def _message_digest(encoded: bytes) -> str:
    return sha256(json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def import_display_history(messages: Sequence[ModelMessage]) -> DisplayHistory:
    """One ingress for native initial state and version-1 display envelopes.

    Imported rows retain their original message/part coordinates. They contain
    presentation data, not serialized native messages or executable model state.
    """
    from a13n_harness_ui.thread_projection import _additional_input_count, _message_entry, _transcript_turns

    completed = tuple(
        position
        for position, message in enumerate(messages)
        if isinstance(message, ModelResponse) and (message.metadata or {}).get(_COMPLETED_KEY) is True
    )
    turns = {turn.input_position: turn for turn in _transcript_turns(tuple(messages), completed)}
    items: list[Item] = []
    for position, message in enumerate(messages):
        entry = _message_entry(position, message).model_dump(mode="json")
        # The transcript DTO bounds previews. Comments resolve the original full
        # text, stored once in this compact row, never a truncated preview.
        if isinstance(message, ModelResponse):
            for index, part in enumerate(message.parts):
                if isinstance(part, TextPart):
                    entry["parts"][index]["text"] = part.content
                    entry["parts"][index]["text_truncated"] = False
        content = {"entry": entry, "steering_count": _additional_input_count((message,))}
        if position in turns:
            content["turn"] = turns[position].model_dump(mode="json")
        items.append(
            Item(
                id=f"import:{position}",
                ordinal=position + 1,
                kind="observation",
                state="completed",
                first_stream_id="0-0",
                last_stream_id="0-0",
                started_at=message.timestamp or datetime.now(UTC),
                content=content,
            )
        )
    return DisplayHistory(
        items=tuple(items),
        completed=tuple(f"import:{position}" for position in completed),
        pending_response=items[-1].id
        if messages and isinstance(messages[-1], ModelResponse) and messages[-1].state == "suspended"
        else None,
    )


def saved_display_history(state: HarnessState) -> DisplayHistory | None:
    return _read_display_history(state, state.agent_context_state.get(_STATE_KEY))


def _read_display_history(state: HarnessState, entry: CapabilityState | None) -> DisplayHistory | None:
    if entry is None:
        return None
    if entry.version == "2":
        return DisplayHistory.model_validate(entry.data)
    if entry.version != "1":
        raise ValueError("Unsupported display history version")
    data = entry.data
    if not isinstance(data, dict):
        raise ValueError("Invalid legacy display history")
    if data.get("model_history_digest") != _message_digest(cast(bytes, state.message_history_json)):
        return None
    messages = decode_messages(json.dumps(data.get("messages", [])).encode())
    positions = data.get("model_positions", [])
    pending = data.get("pending_response_position")
    if (
        not isinstance(positions, list)
        or len(positions) != len(state.message_history)
        or any(
            position is not None and (not isinstance(position, int) or not 0 <= position < len(messages))
            for position in [*positions, pending]
        )
    ):
        raise ValueError("Invalid legacy display history positions")
    return import_display_history(messages)


def with_display_history(state: HarnessState, display: DisplayHistory) -> HarnessState:
    entries = state.agent_context_state.entries
    entries[_STATE_KEY] = CapabilityState(version="2", data=display.model_dump(mode="json"))
    return state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})


def detach_display_history(state: HarnessState) -> tuple[HarnessState, DisplayHistory | None]:
    entries = state.agent_context_state.entries
    entry = entries.pop(_STATE_KEY, None)
    display = _read_display_history(state, entry)
    if entry is None:
        return state, display
    runtime = state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})
    return runtime, display


class DisplayHistoryCollector:
    """Fold producer observations synchronously; publication never owns capture."""

    def __init__(self, model_history: Sequence[ModelMessage], saved: DisplayHistory | None = None) -> None:
        self.saved = saved if saved is not None else import_display_history(model_history)
        self._pending_response = self.saved.pending_response
        self._resume_group_assigned = False
        if self._pending_response is not None and not (
            model_history and isinstance(model_history[-1], ModelResponse) and model_history[-1].state == "suspended"
        ):
            # A provider-boundary checkpoint has removed the suspended native
            # tail. Retry replaces that slot rather than stranding old partial text.
            items = []
            for item in self.saved.items:
                if item.id == self._pending_response or item.content.get("responseGroup") == self._pending_response:
                    content = dict(item.content)
                    if "text" in content:
                        content["text"] = ""
                    entry = content.get("entry")
                    if isinstance(entry, dict):
                        content["entry"] = {**entry, "parts": []}
                    item = item.model_copy(update={"content": content})
                items.append(item)
            self.saved = self.saved.model_copy(update={"items": tuple(items)})
        self.fold: DisplayFold | None = None
        self._published: dict[str, Item] = {}
        self._pending: set[str] = set()

    def observe(self, source: HarnessStreamEvent[Any]) -> None:
        if self.fold is None:
            self.fold = DisplayFold(
                source.run_id,
                self.saved.items,
                attempt=self.saved.position.attempt + 1,
                full_content=True,
            )
            self.fold.changed.clear()
        self.fold.fold(self.fold.events(source), source)
        if (
            self._pending_response is not None
            and not self._resume_group_assigned
            and "root" in self.fold.response_groups
        ):
            self.fold.response_groups["root"] = self._pending_response
            self._resume_group_assigned = True
        self._pending.update(self.fold.changed)
        self.fold.changed.clear()

    def supplement(self, events: Sequence[Any]) -> None:
        if self.fold is not None:
            self.fold.fold([event.model_dump(mode="json", by_alias=True) for event in events])
            self._pending.update(self.fold.changed)
            self.fold.changed.clear()

    def drain(self) -> list[ItemChange]:
        if self.fold is None:
            return []
        changes = []
        fold = self.fold
        for key in sorted(self._pending, key=lambda key: fold.items[key].ordinal):
            item = fold.items[key]
            changes.append(item_change(self._published.get(key), item))
            self._published[key] = item
        self._pending.clear()
        return changes

    def publication_failed(self) -> None:
        self._pending.update(self._published)
        self._published.clear()

    def capture(self, history: Sequence[ModelMessage] = (), *, completed: bool = False) -> DisplayHistory:
        if self.fold is None:
            return self.saved.model_copy(deep=True)
        items = tuple(item.model_copy(deep=True) for item in self.fold.items.values())
        marks = set(self.saved.completed)
        last_input = next((item.ordinal for item in reversed(items) if ordinary_input(item)), 0)
        # Resuming a turn makes its former answer provisional again.
        marks.difference_update(item.id for item in items if item.ordinal >= last_input)
        group = self.fold.response_groups.get("root")
        if completed and group is not None:
            marks.update(
                item.id for item in items if assistant_text(item) and item.content.get("responseGroup") == group
            )
        return DisplayHistory(
            run_id=self.fold.run_id,
            items=items,
            position=self.fold.position,
            completed=tuple(sorted(marks)),
            pending_response=group
            if history and isinstance(history[-1], ModelResponse) and history[-1].state == "suspended"
            else self._pending_response
            if not any(item.content.get("responseGroup") == self._pending_response for item in items)
            else None,
        )


def ordinary_input(item: Item) -> bool:
    if isinstance(item.content.get("turn"), dict):
        return True
    metadata = item.content.get("metadata")
    return (
        item.kind == "text_message"
        and item.content.get("role") == "user"
        and item.content.get("input_source", "user") == "user"
        and not item.content.get("subagentRunId")
        and not (isinstance(metadata, dict) and metadata.get("display") is False)
    )


def assistant_text(item: Item) -> bool:
    return (
        item.kind == "text_message"
        and item.content.get("role", "assistant") == "assistant"
        and not item.content.get("subagentRunId")
        and isinstance(item.content.get("text"), str)
    )
