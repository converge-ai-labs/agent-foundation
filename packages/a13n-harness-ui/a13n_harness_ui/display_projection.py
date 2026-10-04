"""Present compact items through the existing transcript and comment contracts."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from a13n_stream_protocol.display import Item

from a13n_harness_ui.conversation import excerpt_text
from a13n_harness_ui.display_history import DisplayHistory, ordinary_input
from a13n_harness_ui.output_comment_models import RootOutputLocation, SavedOutputTarget
from a13n_harness_ui.storage import Thread
from a13n_harness_ui.surfaces import TranscriptEntry, TranscriptPart, TranscriptTurn


@dataclass
class _Row:
    items: list[Item] = field(default_factory=list)
    parts: list[dict[str, Any]] = field(default_factory=list)
    kind: Literal["request", "response"] = "response"


def _parts(item: Item) -> list[dict[str, Any]]:
    content = item.content
    metadata = content.get("metadata") or {}
    if item.kind in {"text_message", "reasoning_message"}:
        media = content.get("input_media")
        return [
            {
                "kind": "media"
                if media is not None
                else "thinking"
                if item.kind == "reasoning_message"
                else "user"
                if content.get("role") == "user"
                else "assistant",
                "text": json.dumps(media, ensure_ascii=False) if media is not None else str(content.get("text", "")),
                "metadata": metadata,
            }
        ]
    if item.kind == "tool_call":
        common = {
            "tool_name": content.get("toolCallName"),
            "tool_call_id": content.get("toolCallId"),
            "provider": content.get("provider"),
        }
        parts = [{"kind": "tool_call", "value": content.get("arguments"), **common}]
        # Reserve the result coordinate with the call: a later result must not
        # renumber assistant parts that already have saved comment targets.
        parts.append(
            {
                "kind": "retry" if content.get("retry") else "tool_result",
                **common,
                "outcome": content.get("outcome")
                or ("failed" if item.state == "failed" else "success" if item.state == "completed" else None),
                "value": content.get("value", content.get("result", content.get("failure"))),
                "content_parts": content.get("result_parts", []),
                "text": str(content.get("value", "")) if content.get("retry") else None,
                "applied_edit": content.get("applied_edit"),
                "tool_images": content.get("tool_images", []),
                "mcp_apps": content.get("mcp_apps", []),
                "tool_image_unavailable": content.get("tool_image_unavailable", False),
                "value_omitted": content.get("truncated", False),
            }
        )
        return parts
    name = content.get("name")
    value = content.get("value")
    event = value.get("event") if isinstance(value, dict) else None
    if not isinstance(event, dict):
        return []
    if name in {"a13n.context.handoff_summary", "a13n.context.compaction_summary"}:
        return [
            {
                "kind": "assistant",
                "text": event.get("summary", ""),
                "metadata": {
                    "a13n.context": "handoff" if name == "a13n.context.handoff_summary" else "compaction",
                    "operation_id": event.get("operation_id"),
                },
            }
        ]
    if isinstance(name, str) and name.startswith("a13n.input."):
        return [{"kind": "user", "text": str(event.get("content", "")), "metadata": metadata}]
    return []


def _rows(display: DisplayHistory) -> list[_Row]:
    rows: OrderedDict[str, _Row] = OrderedDict()
    for item in display.items:
        content = item.content
        if content.get("subagentRunId"):
            continue
        imported = content.get("entry")
        if isinstance(imported, dict):
            rows[f"response:{item.id}"] = _Row(
                [item],
                list(cast(list[dict[str, Any]], imported["parts"])),
                "request" if imported["message_kind"] == "request" else "response",
            )
            continue
        parts = _parts(item)
        if not parts:
            continue
        group = str(content.get("input_group") or content.get("responseGroup") or item.id)
        # Input and response identifiers are separate identity domains.
        user = item.kind == "text_message" and content.get("role") == "user"
        key = f"{'input' if user else 'response'}:{group}"
        row = rows.setdefault(key, _Row(kind="request" if user else "response"))
        row.items.append(item)
        row.parts.extend(parts)
    return list(rows.values())


def original_text(display: DisplayHistory, message: int, part: int) -> str | None:
    rows = _rows(display)
    if message >= len(rows) or part >= len(rows[message].parts):
        return None
    source = rows[message].parts[part]
    metadata = source.get("metadata") or {}
    if source["kind"] != "assistant" or metadata.get("a13n.context") or metadata.get("display") is False:
        return None
    text = source.get("text")
    return text if isinstance(text, str) else None


def display_entries(display: DisplayHistory, thread: Thread | None = None) -> tuple[TranscriptEntry, ...]:
    entries = []
    for position, row in enumerate(_rows(display)):
        parts = []
        for index, source in enumerate(row.parts):
            data = dict(source)
            metadata = data.get("metadata") or {}
            text = data.get("text")
            if isinstance(text, str) and len(text) > 65536 and not metadata.get("a13n.context"):
                data.update(text=text[:65513] + "\n...[content truncated]", text_truncated=True)
            if (
                data["kind"] == "assistant"
                and not metadata.get("a13n.context")
                and metadata.get("display") is not False
                and thread is not None
                and thread.parent_thread_id is None
                and thread.continuation is not None
            ):
                data["comment_target"] = SavedOutputTarget(
                    producing_thread_id=thread.thread_id,
                    source_id=thread.continuation.logical_digest,
                    location=RootOutputLocation(message=position, part=index),
                )
            parts.append(TranscriptPart.model_validate(data, strict=False))
        entries.append(
            TranscriptEntry(
                position=position,
                message_kind=row.kind,
                timestamp=row.items[0].started_at,
                parts=tuple(parts),
            )
        )
    return tuple(entries)


def display_turns(display: DisplayHistory) -> tuple[TranscriptTurn, ...]:
    rows = _rows(display)
    inputs = [(index, row) for index, row in enumerate(rows) if any(ordinary_input(item) for item in row.items)]
    turns = []
    for index, (start, first) in enumerate(inputs):
        end = inputs[index + 1][0] if index + 1 < len(inputs) else len(rows)
        section = rows[start:end]
        final = next(
            (
                start + offset
                for offset, row in reversed(list(enumerate(section)))
                if any(item.id in display.completed for item in row.items)
            ),
            None,
        )
        closing = section[-1]
        output = (
            end - 1
            if (
                any(
                    part["kind"] == "assistant" and not (part.get("metadata") or {}).get("a13n.context")
                    for part in closing.parts
                )
                and not any(part["kind"] == "tool_call" for part in closing.parts)
            )
            else None
        )
        if final is not None:
            output = final
        source = first.items[0].content
        imported = source.get("turn")
        preview = " ".join(
            str(part.get("text") or "[attachment]")
            for part in first.parts
            if part["kind"] in {"user", "media"} and (part.get("metadata") or {}).get("display") is not False
        )
        turns.append(
            TranscriptTurn(
                turn_id=str(
                    imported["turn_id"]
                    if isinstance(imported, dict)
                    else source.get("input_group") or first.items[0].id
                ),
                input_position=start,
                end_position=end,
                final_position=final,
                output_position=output,
                output_preview=excerpt_text(
                    " ".join(str(part.get("text") or "") for part in rows[output].parts if part["kind"] == "assistant"),
                    512,
                )
                if output is not None
                else None,
                preview=excerpt_text(preview, 512),
                timestamp=first.items[0].started_at,
                tool_count=sum(part["kind"] == "tool_call" for row in section for part in row.parts),
                steering_count=sum(
                    cast(int, item.content.get("steering_count", 0)) for row in section for item in row.items
                )
                + len(
                    {
                        str(item.content.get("input_group") or item.id)
                        for row in section
                        for item in row.items
                        if item.content.get("input_source") == "steering"
                    }
                ),
                app_positions=tuple(
                    start + offset
                    for offset, row in enumerate(section)
                    if any(part.get("mcp_apps") for part in row.parts)
                ),
            )
        )
    return tuple(turns)
