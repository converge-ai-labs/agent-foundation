"""Harness UI display producer baselines; semantic projection is shared."""

from __future__ import annotations

from uuid import uuid4

from a13n_harness import HarnessEvent
from a13n_stream_protocol.display import DisplayPosition, DisplaySnapshot, Producer
from a13n_stream_protocol.session import DisplayCapture, HistoryCursor
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

_METADATA = TypeAdapter(dict[str, JsonValue])


def enrich_tool(display: DisplayCapture, item: object) -> None:
    """Publish Host-retained metadata without folding native result semantics again."""
    if not isinstance(item, HarnessEvent) or not isinstance(item.event, FunctionToolResultEvent):
        return
    part = item.event.part
    session = display.sessions.get(item.run_id)
    if not isinstance(part, ToolReturnPart) or not isinstance(part.metadata, dict) or session is None:
        return
    scope = session.cursor.tool_scopes.get(part.tool_call_id, item.run_id)
    display.projector.enrich_tool(scope, part.tool_call_id, _METADATA.validate_python(part.metadata))


def baseline(run_id: str, saved: DisplaySnapshot | None = None) -> DisplaySnapshot:
    position = DisplayPosition(producer=Producer(run_id=run_id, generation=uuid4().hex))
    if saved is None:
        return DisplaySnapshot(position=position)
    return saved.model_copy(deep=True, update={"position": position})


def clear_context(display: DisplaySnapshot, thread_id: str) -> DisplaySnapshot:
    """Drop native positional continuity without reusing saved output addresses."""
    continuity = dict(display.continuity)
    cursor = HistoryCursor.model_validate(continuity.get(thread_id, {}))
    continuity[thread_id] = HistoryCursor(next_index=cursor.next_index).model_dump(mode="json")
    return display.model_copy(deep=True, update={"continuity": continuity})
