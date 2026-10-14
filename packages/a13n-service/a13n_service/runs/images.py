"""Capture explicitly exposed screenshots at the producer, retaining bytes at the checkpoint boundary."""

from typing import Any

from a13n_harness import HarnessEvent, HarnessStreamEvent
from pydantic_ai import BinaryContent
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

from a13n_service.runs.contents import Contents


def observe(source: HarnessStreamEvent[Any], *, root_run_id: str | None, contents: Contents) -> dict[str, Any] | None:
    if not isinstance(source, HarnessEvent) or not isinstance(source.event, FunctionToolResultEvent):
        return None
    part = source.event.part
    if not isinstance(part, ToolReturnPart) or not isinstance(part.metadata, dict):
        return None
    if part.metadata.get("a13n.computer.screenshot") is not True:
        return None
    images, unavailable = [], False
    values = part.content if isinstance(part.content, list | tuple) else source.event.content
    for value in values if isinstance(values, list | tuple) else ():
        if not isinstance(value, BinaryContent) or value.media_type not in {"image/png", "image/jpeg"}:
            continue
        image = contents.image(value.data, "image/png" if value.media_type == "image/png" else "image/jpeg")
        if image is None:
            unavailable = True
        else:
            images.append(image.model_dump(mode="json"))
    event: dict[str, Any] = {
        "type": "CUSTOM",
        "name": "a13n.tool_images",
        "value": {"event": {"tool_call_id": part.tool_call_id, "images": images, "unavailable": unavailable}},
    }
    if source.run_id != root_run_id:
        event["subagentRunId"] = source.run_id
    return event
