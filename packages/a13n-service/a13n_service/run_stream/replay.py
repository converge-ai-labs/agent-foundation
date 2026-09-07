"""Create-only retained replay snapshots for closed, complete Run Streams."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from itertools import pairwise
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from a13n_service.storage import ObjectConflict, ObjectInfo, ObjectNotFound, ObjectStore, ObjectStoreError
from a13n_service.storage.codec import DurableObjectCodecError, canonical_model_bytes, decode_canonical_model

from .domain import (
    CompleteRunStream,
    RetainedItem,
    RetainedReplayUnavailable,
    RetainedRunStreamEvent,
    RunReplaySnapshot,
    RunStreamEntry,
    RunStreamError,
)
from .redis import run_stream_key_digest_sha256

RUN_REPLAY_CONTENT_TYPE = "application/vnd.converge.run-replay+json"
_SNAPSHOT_ADAPTER = TypeAdapter(RunReplaySnapshot)
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class RunReplayIntegrityError(RunStreamError):
    """Retained replay bytes or metadata violate the immutable contract."""


class RunReplayStore:
    def __init__(
        self,
        objects: ObjectStore,
        *,
        max_events: int = 4096,
        max_items: int = 2048,
        max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if min(max_events, max_items, max_bytes) < 1:
            raise ValueError("retained replay bounds must be positive")
        self._objects = objects
        self._max_events = max_events
        self._max_items = max_items
        self._max_bytes = max_bytes

    async def publish(
        self,
        organization_id: str,
        run_id: str,
        source: CompleteRunStream,
    ) -> RunReplaySnapshot:
        if not source.entries or len(source.entries) > self._max_events:
            raise RetainedReplayUnavailable("Run Stream event count is outside the retained replay bounds")
        first = source.entries[0].event
        if first.run_id != run_id:
            raise RunReplayIntegrityError("Run Stream source identity does not match the selected Run")
        expected_stream_digest = run_stream_key_digest_sha256(organization_id, run_id)
        if source.stream_key_digest_sha256 != expected_stream_digest:
            raise RunReplayIntegrityError("Run Stream source key does not match the selected Run")
        items = project_retained_items(source.entries)
        if len(items) > self._max_items:
            raise RetainedReplayUnavailable("Run Stream Item count exceeds the retained replay bound")
        snapshot = RunReplaySnapshot(
            run_id=run_id,
            thread_id=first.thread_id,
            stream_key_digest_sha256=source.stream_key_digest_sha256,
            first_stream_id=source.entries[0].stream_id,
            last_stream_id=source.entries[-1].stream_id,
            closed_at=source.closed_at,
            source_run_attempt_ids=_attempt_ids(source.entries),
            events=tuple(
                RetainedRunStreamEvent(stream_id=entry.stream_id, event=entry.event) for entry in source.entries
            ),
            items=items,
        )
        _validate_snapshot_body(snapshot)
        body = canonical_model_bytes(snapshot)
        if len(body) > self._max_bytes:
            raise RetainedReplayUnavailable("retained replay exceeds its encoded size bound")
        digest = hashlib.sha256(body).hexdigest()
        key = run_replay_key(organization_id, run_id)
        metadata = {"schema-version": "1", "run-id": run_id, "digest-sha256": digest}
        try:
            info = await self._objects.put(
                key,
                body,
                content_type=RUN_REPLAY_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=True,
            )
        except ObjectConflict as error:
            existing = await self.read(organization_id, run_id)
            if existing != snapshot:
                raise RunReplayIntegrityError("existing retained replay does not match the complete source") from error
            return existing
        _verify_info(info, key=key, body=body, metadata=metadata)
        return snapshot

    async def read(
        self,
        organization_id: str,
        run_id: str,
        *,
        expected_thread_id: str | None = None,
    ) -> RunReplaySnapshot:
        key = run_replay_key(organization_id, run_id)
        try:
            body, info = await _read_object(self._objects, key, max_bytes=self._max_bytes)
        except ObjectNotFound as error:
            raise RetainedReplayUnavailable("retained Run replay is unavailable") from error
        except ObjectStoreError as error:
            raise RunReplayIntegrityError("retained Run replay could not be read") from error
        digest = hashlib.sha256(body).hexdigest()
        metadata = {"schema-version": "1", "run-id": run_id, "digest-sha256": digest}
        _verify_info(info, key=key, body=body, metadata=metadata)
        try:
            snapshot = decode_canonical_model(body, _SNAPSHOT_ADAPTER)
        except DurableObjectCodecError as error:
            raise RunReplayIntegrityError("retained replay body is invalid") from error
        if snapshot.run_id != run_id or (expected_thread_id is not None and snapshot.thread_id != expected_thread_id):
            raise RunReplayIntegrityError("retained replay body belongs to another Run")
        if snapshot.stream_key_digest_sha256 != run_stream_key_digest_sha256(organization_id, run_id):
            raise RunReplayIntegrityError("retained replay Stream identity is invalid")
        _validate_snapshot_body(snapshot)
        return snapshot


def run_replay_key(organization_id: str, run_id: str) -> str:
    return f"organizations/{organization_id}/runs/{run_id}/replay/version-1.json"


def project_retained_items(entries: tuple[RunStreamEntry, ...]) -> tuple[RetainedItem, ...]:
    item_entries: dict[str, list[RunStreamEntry]] = {}
    for entry in entries:
        if entry.event.item_id is not None:
            item_entries.setdefault(entry.event.item_id, []).append(entry)
    return tuple(_retained_item(item_id, values) for item_id, values in item_entries.items())


def _retained_item(item_id: str, entries: list[RunStreamEntry]) -> RetainedItem:
    first = entries[0]
    last = entries[-1]
    kind = first.event.payload.get("item_kind")
    if not isinstance(kind, str) or not kind:
        kind = first.event.event_type.split(".", maxsplit=1)[-1]
    parent = first.event.payload.get("parent_item_id")
    if not isinstance(parent, str):
        parent = None
    content: JsonValue
    if kind == "run_output":
        content = last.event.payload.get("content")
        if not isinstance(content, dict):
            raise RunReplayIntegrityError("terminal Run output Item omitted its selected content")
    else:
        content = _JSON_VALUE_ADAPTER.validate_python(
            {"events": [{"event_type": entry.event.event_type, "payload": entry.event.payload} for entry in entries]},
            strict=True,
        )
    return RetainedItem(
        id=item_id,
        kind=kind,
        state=_item_state(last),
        parent_item_id=parent,
        first_stream_id=first.stream_id,
        last_stream_id=last.stream_id,
        content=content,
    )


def _item_state(entry: RunStreamEntry) -> Literal["completed", "interrupted", "failed"]:
    explicit = entry.event.payload.get("item_state")
    if explicit == "completed":
        return "completed"
    if explicit == "failed":
        return "failed"
    if explicit == "interrupted":
        return "interrupted"
    if entry.event.event_type == "item.failed":
        return "failed"
    if entry.event.event_type in {
        "agui.text_message_end",
        "agui.reasoning_message_end",
        "agui.tool_call_result",
        "item.completed",
    }:
        return "completed"
    return "interrupted"


def _attempt_ids(entries: tuple[RunStreamEntry, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for entry in entries:
        attempt_id = entry.event.run_attempt_id
        if attempt_id is not None and attempt_id not in result:
            result.append(attempt_id)
    return tuple(result)


def _validate_snapshot_body(snapshot: RunReplaySnapshot) -> None:
    entries = tuple(RunStreamEntry(item.stream_id, item.event) for item in snapshot.events)
    positions = tuple(_stream_position(entry.stream_id) for entry in entries)
    if any(current <= previous for previous, current in pairwise(positions)):
        raise RunReplayIntegrityError("retained replay events are not strictly ordered")
    event_ids = tuple(entry.event.event_id for entry in entries)
    if len(event_ids) != len(set(event_ids)):
        raise RunReplayIntegrityError("retained replay contains duplicate event identities")
    if snapshot.source_run_attempt_ids != _attempt_ids(entries):
        raise RunReplayIntegrityError("retained replay Attempt index does not match its events")
    if snapshot.items != project_retained_items(entries):
        raise RunReplayIntegrityError("retained replay Item index does not match its events")


def _stream_position(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", maxsplit=1)
    return int(milliseconds), int(sequence)


async def _read_object(objects: ObjectStore, key: str, *, max_bytes: int) -> tuple[bytes, ObjectInfo]:
    async with objects.open(key) as reader:
        info = reader.info
        if info.size > max_bytes:
            raise RetainedReplayUnavailable("retained replay exceeds its encoded size bound")
        chunks: list[bytes] = []
        size = 0
        async for chunk in reader:
            size += len(chunk)
            if size > max_bytes:
                raise RetainedReplayUnavailable("retained replay exceeds its encoded size bound")
            chunks.append(chunk)
    body = b"".join(chunks)
    if len(body) != info.size:
        raise RunReplayIntegrityError("retained replay size does not match object metadata")
    return body, info


def _verify_info(info: ObjectInfo, *, key: str, body: bytes, metadata: Mapping[str, str]) -> None:
    if (
        info.key != key
        or info.size != len(body)
        or info.content_type != RUN_REPLAY_CONTENT_TYPE
        or not info.version
        or any(info.metadata.get(name) != value for name, value in metadata.items())
    ):
        raise RunReplayIntegrityError("retained replay object metadata is invalid")


__all__ = [
    "RUN_REPLAY_CONTENT_TYPE",
    "RunReplayIntegrityError",
    "RunReplayStore",
    "project_retained_items",
    "run_replay_key",
]
