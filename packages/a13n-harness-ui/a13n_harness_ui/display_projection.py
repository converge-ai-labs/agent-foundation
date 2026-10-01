"""Presentation-only mapping from shared display snapshots to UI transcript rows."""

from __future__ import annotations

import json
from datetime import datetime

from a13n_stream_protocol.display import DisplayBlock, DisplaySnapshot
from a13n_stream_protocol.messages import ContentMetadata
from pydantic_ai.messages import ToolReturnPart

from a13n_harness_ui.conversation import excerpt_text
from a13n_harness_ui.mcp_apps.snapshots import app_references
from a13n_harness_ui.output_comment_models import RootOutputLocation, SavedOutputTarget
from a13n_harness_ui.surfaces import TranscriptEntry, TranscriptPart, TranscriptTurn
from a13n_harness_ui.tool_evidence import applied_edit
from a13n_harness_ui.tool_images import tool_image_unavailable, tool_images


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _parts(
    block: DisplayBlock, *, thread_id: str, source_id: str | None, own_scope: bool
) -> tuple[TranscriptPart, ...]:
    content = block.content
    text = _text(content.get("text", ""))
    metadata = ContentMetadata.from_native(content.get("metadata") if isinstance(content.get("metadata"), dict) else {})
    if block.kind in {"input", "text", "reasoning", "context_summary", "media"}:
        if block.kind == "context_summary":
            metadata = ContentMetadata.from_native(
                {"a13n.context": content.get("kind", "compaction"), "operation_id": content.get("operation_id")}
            )
        kind = (
            "user"
            if block.kind == "input"
            else "thinking"
            if block.kind == "reasoning"
            else "media"
            if block.kind == "media"
            else "assistant"
        )
        if block.kind == "media" and not text:
            text = _text(content.get("media", {}))
        target = None
        if (
            block.kind == "text"
            and own_scope
            and source_id is not None
            and block.message_index is not None
            and block.part_index is not None
        ):
            target = SavedOutputTarget(
                producing_thread_id=thread_id,
                source_id=source_id,
                location=RootOutputLocation(message=block.message_index, part=block.part_index),
            )
        context = block.kind == "context_summary"
        return (
            TranscriptPart(
                kind=kind,
                text=text if context else text[:65536],
                text_truncated=not context and len(text) > 65536,
                metadata=metadata,
                comment_target=target,
            ),
        )
    if block.kind == "tool_chunk":
        name = str(content.get("name", "unknown"))
        provider = str(content.get("provider") or "native") if content.get("native") else None
        call_id = str(content.get("tool_call_id", block.id))
        arguments = content.get("arguments", "")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        parts = [
            TranscriptPart(
                kind="tool_call",
                tool_name=name,
                tool_call_id=call_id,
                value=arguments,
                provider=provider,
            )
        ]
        if "result" in content:
            # Reuse the metadata readers; no execution semantics are reconstructed.
            result = ToolReturnPart(
                tool_name=name, tool_call_id=call_id, content=content["result"], metadata=content.get("metadata")
            )
            content_parts = content.get("content_parts")
            parts.append(
                TranscriptPart(
                    kind="retry" if content.get("retry") is True else "tool_result",
                    text=_text(content["result"]) if content.get("retry") is True else None,
                    tool_name=name,
                    tool_call_id=call_id,
                    provider=provider,
                    value=content["result"],
                    content_parts=tuple(item for item in content_parts if isinstance(item, dict))
                    if isinstance(content_parts, list)
                    else (),
                    outcome="interrupted"
                    if block.status == "cancelled" or content.get("outcome") == "interrupted"
                    else "denied"
                    if content.get("outcome") == "denied"
                    else "success"
                    if block.status == "succeeded"
                    else "interrupted"
                    if block.status == "cancelled"
                    else "failed",
                    applied_edit=applied_edit(result),
                    tool_images=tool_images(result),
                    mcp_apps=app_references(result),
                    tool_image_unavailable=tool_image_unavailable(result),
                )
            )
        return tuple(parts)
    return (TranscriptPart(kind="other", value=content),)


def transcript(
    snapshot: DisplaySnapshot, *, thread_id: str, source_id: str | None
) -> tuple[tuple[TranscriptEntry, ...], tuple[TranscriptTurn, ...]]:
    scopes = {scope.id: scope for scope in snapshot.scopes}
    groups: dict[tuple[str, int | str], list[DisplayBlock]] = {}
    for block in snapshot.blocks:
        key = (block.scope_id, block.message_index if block.message_index is not None else block.id)
        groups.setdefault(key, []).append(block)
    entries: list[TranscriptEntry] = []
    inputs: list[tuple[int, str, str]] = []
    root_outputs: set[int] = set()
    completed_outputs: set[int] = set()
    steering: set[int] = set()
    root_native: set[int] = set()
    root_scopes: dict[int, str] = {}
    # A request summary can be observed before canonical input capture. Put
    # that scope's opening input before its prefix summaries in history pages,
    # without changing producer ordering or display coverage.
    ordered: list[list[DisplayBlock]] = []
    pending: dict[str, list[list[DisplayBlock]]] = {}
    entered: set[str] = set()
    for blocks in groups.values():
        first = blocks[0]
        # A completed prefix-only scope must stay before subsequent Runs.
        # Otherwise a later snapshot would move its saved rows to the tail.
        for scope_id in tuple(pending):
            if scope_id != first.scope_id:
                ordered.extend(pending.pop(scope_id))
        scope = scopes[first.scope_id]
        if scope.thread_id == thread_id and scope.parent_scope_id is None and scope.id not in entered:
            if first.message_index is None:
                pending.setdefault(scope.id, []).append(blocks)
                continue
            entered.add(scope.id)
            prefix = pending.pop(scope.id, [])
            if any(block.kind == "input" or block.content.get("message_kind") == "request" for block in blocks):
                ordered.extend([blocks, *prefix])
            else:
                ordered.extend([*prefix, blocks])
        else:
            ordered.append(blocks)
    ordered.extend(blocks for prefix in pending.values() for blocks in prefix)
    for blocks in ordered:
        position = len(entries)
        first = blocks[0]
        scope = scopes[first.scope_id]
        own_scope = scope.thread_id == thread_id and scope.parent_scope_id is None
        if own_scope:
            root_scopes[position] = scope.id
            if first.message_index is not None:
                root_native.add(position)
        parts = tuple(
            part
            for block in blocks
            for part in _parts(block, thread_id=thread_id, source_id=source_id, own_scope=own_scope)
        )
        stamp = first.content.get("timestamp")
        timestamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
        user = [
            block
            for block in blocks
            if block.kind == "input" or (block.kind == "media" and block.content.get("message_kind") == "request")
        ]
        request = bool(user) or first.content.get("message_kind") == "request"
        entries.append(
            TranscriptEntry(
                position=position, message_kind="request" if request else "response", timestamp=timestamp, parts=parts
            )
        )
        if user and own_scope:
            message_metadata = user[0].content.get("message_metadata")
            message_metadata = message_metadata if isinstance(message_metadata, dict) else {}
            if "a13n.steering-run" in message_metadata:
                if message_metadata.get("a13n.steering-source") not in {"background_process", "async_subagent"}:
                    steering.add(position)
            elif not message_metadata.get("a13n.context"):
                visible = [part for part in parts if part.kind in {"user", "media"}]
                preview = excerpt_text(" ".join(part.text or "" for part in visible), 512)
                source = next((part.metadata.source_id for part in visible if part.metadata.source_id), first.id)
                if preview:
                    inputs.append((position, source, preview))
        if (
            own_scope
            and any(block.kind == "text" and block.status == "succeeded" for block in blocks)
            and not any(
                isinstance(metadata := block.content.get("message_metadata"), dict)
                and (metadata.get("a13n.context") or metadata.get("keep") == "compact")
                for block in blocks
            )
            and not any(block.kind == "tool_chunk" for block in blocks)
        ):
            root_outputs.add(position)
            if scope.status == "completed":
                completed_outputs.add(position)
    turns: list[TranscriptTurn] = []
    for index, (position, identity, preview) in enumerate(inputs):
        end = inputs[index + 1][0] if index + 1 < len(inputs) else len(entries)
        last_native = next((item for item in range(end - 1, position, -1) if item in root_native), None)
        output = last_native if last_native in root_outputs else None
        last_scope = next((root_scopes[item] for item in range(end - 1, position - 1, -1) if item in root_scopes), None)
        final = output if output in completed_outputs and root_scopes[output] == last_scope else None
        turns.append(
            TranscriptTurn(
                turn_id=identity,
                input_position=position,
                end_position=end,
                preview=preview,
                timestamp=entries[position].timestamp,
                output_position=output,
                final_position=final,
                output_preview=excerpt_text(
                    " ".join(part.text or "" for part in entries[output].parts if part.kind == "assistant"), 512
                )
                if output is not None
                else None,
                app_positions=tuple(
                    entry.position for entry in entries[position:end] if any(part.mcp_apps for part in entry.parts)
                ),
                tool_count=sum(part.kind == "tool_call" for entry in entries[position:end] for part in entry.parts),
                steering_count=sum(position <= item < end for item in steering),
            )
        )
    return tuple(entries), tuple(turns)


def child_presentation(snapshot: DisplaySnapshot, final_answer: str | None = None):
    """Project retained child blocks without folding another event transcript."""
    from a13n_harness_ui.storage.contracts import CompactChildActivity, CompactChildDisplay

    activities = []
    apps = {}
    scopes = {scope.id: scope for scope in snapshot.scopes}
    for block in snapshot.blocks:
        scope = scopes[block.scope_id]
        inline_run = scope.run_id if scope.parent_scope_id is not None else None
        content = block.content
        if block.kind in {"text", "reasoning"}:
            text = content.get("text")
            if isinstance(text, str) and text:
                activities.append(
                    CompactChildActivity(
                        kind="thinking" if block.kind == "reasoning" else "text",
                        text=text[:32768],
                        subagent_run_id=inline_run,
                    )
                )
        elif block.kind == "tool_chunk":
            arguments = content.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            content_parts = content.get("content_parts")
            activities.append(
                CompactChildActivity(
                    kind="tool",
                    subagent_run_id=inline_run,
                    content_parts=tuple(item for item in content_parts if isinstance(item, dict))
                    if isinstance(content_parts, list)
                    else (),
                    tool_name=str(content.get("name", "unknown"))[:128],
                    arguments=arguments,
                    result=content.get("result"),
                )
            )
            result = ToolReturnPart(
                tool_name=str(content.get("name", "unknown")),
                tool_call_id=str(content.get("tool_call_id", block.id)),
                content=content.get("result"),
                metadata=content.get("metadata"),
            )
            for app in app_references(result):
                apps[app.app_id] = app
    return CompactChildDisplay(
        activities=tuple(activities[-512:]),
        mcp_apps=tuple(apps.values())[-128:],
        final_answer=final_answer,
    )
