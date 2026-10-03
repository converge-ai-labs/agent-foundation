"""Render compact display items as paged transcript rows without native replay."""

from __future__ import annotations

import json
from typing import Literal, TypedDict, cast

from a13n_stream_protocol.display import Item
from a13n_stream_protocol.messages import ContentMetadata
from pydantic import JsonValue

from a13n_harness_ui.conversation import _projected_excerpt, excerpt_text
from a13n_harness_ui.display_history import DisplayHistory, assistant_text, ordinary_input, response_parts
from a13n_harness_ui.mcp_apps.models import AppReference
from a13n_harness_ui.output_comment_models import RootOutputLocation, SavedOutputTarget
from a13n_harness_ui.storage import Thread
from a13n_harness_ui.surfaces import AppliedEditView, ToolImageView, TranscriptEntry, TranscriptPart, TranscriptTurn


class _ToolFields(TypedDict):
    tool_name: str | None
    tool_call_id: str | None
    provider: str | None


def display_entry(item: Item, thread: Thread) -> TranscriptEntry:
    content = item.content
    metadata = ContentMetadata.from_native(content.get("metadata") if isinstance(content.get("metadata"), dict) else {})
    parts: tuple[TranscriptPart, ...] = ()
    user = item.kind == "text_message" and content.get("role") == "user"
    text = content.get("text")
    if item.kind in {"text_message", "reasoning_message"}:
        if "input_media" in content:
            parts = (
                TranscriptPart(
                    kind="media", text=json.dumps(content["input_media"], ensure_ascii=False), metadata=metadata
                ),
            )
        elif isinstance(text, str):
            target = (
                SavedOutputTarget(
                    producing_thread_id=thread.thread_id,
                    source_id=thread.continuation.logical_digest,
                    location=RootOutputLocation(message=item.ordinal - 1, part=0),
                )
                if thread.continuation is not None and assistant_text(item)
                else None
            )
            parts = (
                TranscriptPart(
                    kind="user" if user else "thinking" if item.kind == "reasoning_message" else "assistant",
                    text=text if len(text) <= 65536 else text[:65513] + "\n...[content truncated]",
                    text_truncated=len(text) > 65536,
                    metadata=metadata,
                    comment_target=target,
                ),
            )
    elif item.kind == "tool_call":
        name = content.get("toolCallName")
        call = content.get("toolCallId")
        provider = content.get("provider")
        common: _ToolFields = {
            "tool_name": name if isinstance(name, str) else None,
            "tool_call_id": call if isinstance(call, str) else None,
            "provider": provider if isinstance(provider, str) else None,
        }
        parts = (TranscriptPart(kind="tool_call", value=content.get("arguments"), **common),)
        if item.state in {"completed", "failed"}:
            outcome = content.get("outcome")
            result_parts = content.get("result_parts")
            images = content.get("tool_images")
            apps = content.get("mcp_apps")
            parts += (
                TranscriptPart(
                    kind="retry" if content.get("retry") else "tool_result",
                    text=str(content.get("value", ""))[:65536] if content.get("retry") else None,
                    **common,
                    outcome=cast(Literal["success", "failed", "denied", "interrupted"], outcome)
                    if outcome in ("success", "failed", "denied", "interrupted")
                    else "failed"
                    if item.state == "failed"
                    else "success",
                    value=content.get("value", content.get("result", content.get("failure"))),
                    content_parts=tuple(
                        cast(dict[str, JsonValue], value) for value in result_parts if isinstance(value, dict)
                    )
                    if isinstance(result_parts, list)
                    else (),
                    applied_edit=AppliedEditView.model_validate(content["applied_edit"])
                    if "applied_edit" in content
                    else None,
                    tool_images=tuple(ToolImageView.model_validate(value) for value in images)
                    if isinstance(images, list)
                    else (),
                    mcp_apps=tuple(AppReference.model_validate_json(json.dumps(value)) for value in apps)
                    if isinstance(apps, list)
                    else (),
                    tool_image_unavailable=content.get("tool_image_unavailable") is True,
                ),
            )
    elif content.get("name") in {"a13n.input.context", "a13n.input.recovery"}:
        value = content.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        if isinstance(event, dict) and isinstance(text := event.get("content"), str):
            parts = (
                TranscriptPart(
                    kind="system",
                    text=text[:65536],
                    text_truncated=len(text) > 65536,
                    metadata=metadata.model_copy(update={"display": False}),
                ),
            )
    elif content.get("name") == "a13n.input.system":
        value = content.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        part = event.get("part") if isinstance(event, dict) else None
        if isinstance(part, dict) and isinstance(text := part.get("content"), str):
            parts = (TranscriptPart(kind="system", text=text[:65536]),)
    elif content.get("name") in {"a13n.context.handoff_summary", "a13n.context.compaction_summary"}:
        value = content.get("value")
        event = value.get("event") if isinstance(value, dict) else None
        if isinstance(event, dict) and isinstance(summary := event.get("summary"), str):
            context = "handoff" if content["name"] == "a13n.context.handoff_summary" else "compaction"
            parts = (
                TranscriptPart(
                    kind="assistant",
                    text=summary,
                    metadata=ContentMetadata.from_native(
                        {"a13n.context": context, "operation_id": event.get("operation_id")}
                    ),
                ),
            )
    scope = content.get("subagentRunId")
    if isinstance(scope, str):
        parts = tuple(part.model_copy(update={"subagent_run_id": scope}) for part in parts)
    return TranscriptEntry(
        position=item.ordinal - 1,
        message_kind="request" if user else "response",
        timestamp=item.started_at,
        parts=parts,
    )


def display_turns(display: DisplayHistory) -> tuple[TranscriptTurn, ...]:
    inputs: list[tuple[Item, str]] = []
    groups: set[str] = set()
    for item in display.items:
        if not ordinary_input(item):
            continue
        group = str(item.content.get("input_group") or item.content.get("messageId") or item.id)
        if group not in groups:
            inputs.append((item, group))
            groups.add(group)
    turns: list[TranscriptTurn] = []
    for index, (first, group) in enumerate(inputs):
        start = first.ordinal - 1
        end = inputs[index + 1][0].ordinal - 1 if index + 1 < len(inputs) else len(display.items)
        items = display.items[start:end]
        final = next((item for item in reversed(items) if item.id in display.completed), None)
        closing = next(
            (
                item
                for item in reversed(items)
                if item.kind in {"text_message", "tool_call"} and not item.content.get("subagentRunId")
            ),
            None,
        )
        output = final or (
            closing
            if closing is not None
            and assistant_text(closing)
            and closing.state == "completed"
            and not (isinstance(metadata := closing.content.get("metadata"), dict) and metadata.get("closing") is False)
            else None
        )
        outputs = response_parts(items, output) if output is not None else ()
        preview = _projected_excerpt(
            (
                media
                if isinstance(media := item.content.get("input_media"), dict)
                else str(item.content.get("text", "[attachment]")),
                ContentMetadata.from_native(item.content.get("metadata") or {}),
            )
            for item in items
            if ordinary_input(item)
            and str(item.content.get("input_group") or item.content.get("messageId") or item.id) == group
        )
        steering = {
            str(item.content.get("input_group") or item.id)
            for item in items
            if item.content.get("input_source") == "steering"
            and not item.content.get("subagentRunId")
            and not (isinstance(metadata := item.content.get("metadata"), dict) and metadata.get("display") is False)
        }
        turns.append(
            TranscriptTurn(
                turn_id=group,
                input_position=start,
                end_position=end,
                final_position=final.ordinal - 1 if final is not None else None,
                output_position=output.ordinal - 1 if output is not None else None,
                output_positions=tuple(item.ordinal - 1 for item in outputs),
                output_preview=excerpt_text("\n\n".join(str(item.content["text"]) for item in outputs), 512)
                if outputs
                else None,
                preview=excerpt_text(preview, 512),
                timestamp=first.started_at,
                tool_count=sum(item.kind == "tool_call" for item in items),
                steering_count=len(steering),
                app_positions=tuple(item.ordinal - 1 for item in items if item.content.get("mcp_apps")),
            )
        )
    return tuple(turns)
