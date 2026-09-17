"""Incremental Item folding without retaining lifetime raw observations."""

from __future__ import annotations

from datetime import datetime

from pydantic import JsonValue

from .display_model import DisplayIntegrityError, DisplayLimitExceeded, RunDisplaySnapshot, stream_position
from .domain import RetainedItem, RunStreamEntry


def project_display(
    previous: RunDisplaySnapshot,
    entries: tuple[RunStreamEntry, ...],
    *,
    max_items: int = 2048,
    closed_at: datetime | None = None,
    incomplete_reason: str | None = None,
) -> RunDisplaySnapshot:
    """Fold a contiguous suffix; the caller verifies Redis continuity before use.

    Restarted reads may overlap the committed cursor. Such entries are skipped,
    while ordering and identity violations in the new suffix fail explicitly.
    """
    if previous.finalized or not previous.complete:
        raise DisplayIntegrityError("settled or incomplete display cannot consume more events")
    items = {item.id: item for item in previous.items}
    attempts = dict.fromkeys(previous.source_run_attempt_ids)
    cursor = previous.cursor
    committed = (0, 0) if cursor is None else stream_position(cursor)
    position = committed
    for entry in entries:
        event = entry.event
        if event.run_id != previous.run_id or event.thread_id != previous.thread_id:
            raise DisplayIntegrityError("display observation belongs to another Run")
        current = stream_position(entry.stream_id)
        if current <= committed:
            continue
        if current <= position:
            raise DisplayIntegrityError("display observations are not strictly ordered")
        if event.run_attempt_id is not None:
            attempts[event.run_attempt_id] = None
        if event.event_type == "run.recovery":
            items = {key: _interrupt(item) for key, item in items.items()}
        if event.item_id is not None:
            items[event.item_id] = _merge_item(items.get(event.item_id), entry)
            if len(items) > max_items:
                raise DisplayLimitExceeded("display Item count exceeds its configured bound")
        cursor, position = entry.stream_id, current
    if closed_at is not None:
        items = {key: _interrupt(item) for key, item in items.items()}
    return RunDisplaySnapshot(
        version=previous.version + 1,
        run_id=previous.run_id,
        thread_id=previous.thread_id,
        stream_key_digest_sha256=previous.stream_key_digest_sha256,
        cursor=cursor,
        complete=incomplete_reason is None,
        incomplete_reason=incomplete_reason,
        finalized=closed_at is not None,
        closed_at=closed_at,
        source_run_attempt_ids=tuple(attempts),
        items=tuple(items.values()),
    )


def _interrupt(item: RetainedItem) -> RetainedItem:
    return item.model_copy(update={"state": "interrupted"}) if item.state == "in_progress" else item


def _merge_item(previous: RetainedItem | None, entry: RunStreamEntry) -> RetainedItem:
    event, payload = entry.event, entry.event.payload
    assert event.item_id is not None
    kind = payload.get("item_kind")
    if not isinstance(kind, str) or not kind:
        raise DisplayIntegrityError("display observation omitted Item kind")
    parent = payload.get("parent_item_id")
    if parent is not None and not isinstance(parent, str):
        raise DisplayIntegrityError("display Item parent identity is invalid")
    if previous is not None and (previous.kind != kind or previous.parent_item_id != parent):
        raise DisplayIntegrityError("display Item correlation changed")
    content: dict[str, JsonValue] = {}
    if previous is not None:
        if not isinstance(previous.content, dict):
            raise DisplayIntegrityError("display Item content must be an object")
        content.update(previous.content)
    if kind == "run_output":
        output = payload.get("content")
        if not isinstance(output, dict):
            raise DisplayIntegrityError("Run output omitted selected content")
        content = dict(output)
    else:
        _merge_content(content, entry)
    state = "in_progress" if previous is None else previous.state
    explicit = payload.get("item_state")
    if isinstance(explicit, str) and explicit in {"completed", "failed", "interrupted"}:
        state = explicit
    elif event.event_type in {"agui.text_message_end", "agui.reasoning_message_end", "agui.tool_call_result"}:
        state = "completed"
    return RetainedItem.model_validate(
        {
            "id": event.item_id,
            "kind": kind,
            "state": state,
            "parent_item_id": parent,
            "first_stream_id": entry.stream_id if previous is None else previous.first_stream_id,
            "last_stream_id": entry.stream_id,
            "content": content,
        }
    )


def _merge_content(content: dict[str, JsonValue], entry: RunStreamEntry) -> None:
    event, payload = entry.event, entry.event.payload
    for field in ("messageId", "role", "toolCallId", "toolCallName", "parentMessageId", "source_tool_call_id"):
        if field in payload:
            content[field] = payload[field]
    content["run_attempt_id"] = event.run_attempt_id
    content["harness_run_id"] = event.harness_run_id
    delta_field = {
        "agui.text_message_content": "text",
        "agui.reasoning_message_content": "text",
        "agui.tool_call_args": "arguments",
    }.get(event.event_type)
    if delta_field is not None:
        delta = payload.get("delta")
        current = content.get(delta_field, "")
        if not isinstance(delta, str) or not isinstance(current, str):
            raise DisplayIntegrityError("display delta accumulator is invalid")
        content[delta_field] = current + delta
    if event.event_type == "agui.tool_call_result":
        content["result"] = payload.get("content")
    if event.event_type == "agui.reasoning_encrypted_value":
        content["encrypted_value"] = payload.get("encryptedValue")
    if "failure" in payload:
        content["failure"] = payload["failure"]
