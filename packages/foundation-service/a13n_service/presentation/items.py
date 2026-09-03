"""Pure reconstruction of retained semantic Items from complete Run Stream events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import JsonValue

from .domain import RetainedItem, RetainedRunStreamEvent, RunOutputItemContent, RunStreamEvent
from .stream import item_id_for_semantic_key

type RetainedItemKind = Literal[
    "message",
    "reasoning",
    "tool_call",
    "tool_result",
    "activity",
    "run_output",
    "error",
]
type RetainedItemState = Literal["completed", "interrupted", "failed"]

_ITEM_KIND_BY_EVENT_TYPE: dict[str, RetainedItemKind] = {
    "TEXT_MESSAGE_START": "message",
    "TEXT_MESSAGE_CONTENT": "message",
    "TEXT_MESSAGE_END": "message",
    "TEXT_MESSAGE_CHUNK": "message",
    "REASONING_START": "reasoning",
    "REASONING_MESSAGE_START": "reasoning",
    "REASONING_MESSAGE_CONTENT": "reasoning",
    "REASONING_MESSAGE_END": "reasoning",
    "REASONING_MESSAGE_CHUNK": "reasoning",
    "REASONING_END": "reasoning",
    "REASONING_ENCRYPTED_VALUE": "reasoning",
    "TOOL_CALL_START": "tool_call",
    "TOOL_CALL_ARGS": "tool_call",
    "TOOL_CALL_END": "tool_call",
    "TOOL_CALL_CHUNK": "tool_call",
    "TOOL_CALL_RESULT": "tool_result",
    "ACTIVITY_SNAPSHOT": "activity",
    "ACTIVITY_DELTA": "activity",
    "RUN_FINISHED": "run_output",
    "RUN_ERROR": "error",
}
_COMPLETED_EVENT_TYPES = frozenset(
    {
        "TEXT_MESSAGE_END",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "ACTIVITY_SNAPSHOT",
        "ACTIVITY_DELTA",
        "RUN_FINISHED",
    }
)


class RetainedItemProjectionError(ValueError):
    """Complete Run Stream events cannot form coherent retained Items."""


def build_retained_items(
    events: Sequence[RetainedRunStreamEvent],
    *,
    terminal_output: RunOutputItemContent | None,
) -> tuple[RetainedItem, ...]:
    """Reconstruct exactly the Items named by the retained event sequence."""

    grouped: dict[str, list[RetainedRunStreamEvent]] = defaultdict(list)
    for retained in events:
        if retained.event.item_id is not None:
            grouped[retained.event.item_id].append(retained)
    result: list[RetainedItem] = []
    for item_id, retained_events in grouped.items():
        unique_events = _deduplicate(retained_events)
        kind = _item_kind(unique_events)
        if kind == "run_output":
            if terminal_output is None:
                raise RetainedItemProjectionError("completed Run output Item is missing selected content")
            content = terminal_output.as_json()
        else:
            content = _item_content(kind, unique_events)
        result.append(
            RetainedItem(
                id=item_id,
                kind=kind,
                state=_item_state(kind, unique_events[-1].event.event_type),
                parent_item_id=_parent_item_id(kind, unique_events, grouped),
                first_stream_id=retained_events[0].stream_id,
                last_stream_id=retained_events[-1].stream_id,
                content=content,
            )
        )
    if terminal_output is not None and not any(item.kind == "run_output" for item in result):
        raise RetainedItemProjectionError("completed Run Stream has no terminal output Item")
    return tuple(result)


def _deduplicate(events: Sequence[RetainedRunStreamEvent]) -> tuple[RetainedRunStreamEvent, ...]:
    unique: list[RetainedRunStreamEvent] = []
    by_id: dict[str, RunStreamEvent] = {}
    for retained in events:
        previous = by_id.get(retained.event.event_id)
        if previous is None:
            by_id[retained.event.event_id] = retained.event
            unique.append(retained)
        elif previous != retained.event:
            raise RetainedItemProjectionError("stable Run Stream event identity has conflicting content")
    return tuple(unique)


def _item_kind(events: Sequence[RetainedRunStreamEvent]) -> RetainedItemKind:
    kinds: set[RetainedItemKind] = set()
    for retained in events:
        kind = _ITEM_KIND_BY_EVENT_TYPE.get(retained.event.event_type)
        if kind is None:
            raise RetainedItemProjectionError("Run Stream event has no retained Item kind")
        kinds.add(kind)
    if len(kinds) != 1:
        raise RetainedItemProjectionError("one Item contains incompatible Run Stream event kinds")
    return next(iter(kinds))


def _item_state(kind: RetainedItemKind, terminal_event_type: str) -> RetainedItemState:
    if kind == "error":
        return "failed"
    return "completed" if terminal_event_type in _COMPLETED_EVENT_TYPES else "interrupted"


def _parent_item_id(
    kind: RetainedItemKind,
    events: Sequence[RetainedRunStreamEvent],
    all_items: dict[str, list[RetainedRunStreamEvent]],
) -> str | None:
    if kind != "tool_result":
        return None
    event = events[-1].event
    tool_call_id = event.payload.get("toolCallId")
    harness_run_id = event.harness_run_id
    if not isinstance(tool_call_id, str) or harness_run_id is None:
        raise RetainedItemProjectionError("tool result Item is missing its tool-call provenance")
    parent = item_id_for_semantic_key(harness_run_id, "tool_call", tool_call_id)
    return parent if parent in all_items else None


def _item_content(
    kind: RetainedItemKind,
    events: Sequence[RetainedRunStreamEvent],
) -> JsonValue:
    payloads = tuple(retained.event.payload for retained in events)
    if kind in {"message", "reasoning"}:
        content = "".join(_string_values(payloads, "delta"))
        role = next(iter(_string_values(payloads, "role")), None)
        return {"content": content, **({"role": role} if role is not None else {})}
    if kind == "tool_call":
        name = next(iter(_string_values(tuple(reversed(payloads)), "toolCallName")), None)
        arguments = "".join(_string_values(payloads, "delta"))
        return {"name": name, "arguments": arguments}
    if kind == "tool_result":
        payload = payloads[-1]
        return {
            "tool_call_id": payload.get("toolCallId"),
            "content": payload.get("content"),
        }
    if kind == "activity":
        return {"events": list(payloads)}
    if kind == "error":
        payload = payloads[-1]
        return {"code": payload.get("code"), "message": payload.get("message")}
    raise RetainedItemProjectionError(f"unsupported retained Item kind: {kind}")


def _string_values(payloads: Sequence[dict[str, JsonValue]], key: str) -> tuple[str, ...]:
    return tuple(value for payload in payloads if isinstance((value := payload.get(key)), str))


__all__ = ["RetainedItemProjectionError", "build_retained_items"]
