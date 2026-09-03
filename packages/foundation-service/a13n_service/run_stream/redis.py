"""Bounded, retry-safe Redis persistence for one Run presentation stream."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import WatchError

from a13n_service.storage.codec import DurableObjectCodecError, canonical_model_bytes, decode_canonical_model

from .domain import (
    CompleteRunStream,
    RetainedReplayUnavailable,
    RunStreamClosed,
    RunStreamEntry,
    RunStreamError,
    RunStreamEvent,
    RunStreamPage,
    RunStreamReplayGap,
)

_EVENT_ADAPTER = TypeAdapter(RunStreamEvent)
_INITIAL_STREAM_ID = "0-0"


class RedisRunStream:
    """One bounded Redis Stream shared by every Attempt of a Run."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_events: int = 4096,
        max_event_bytes: int = 320 * 1024,
        closed_ttl_seconds: int = 24 * 60 * 60,
        transaction_retries: int = 8,
    ) -> None:
        if min(max_events, max_event_bytes, closed_ttl_seconds, transaction_retries) < 1:
            raise ValueError("Run Stream bounds must be positive")
        self._redis = redis
        self._max_events = max_events
        self._max_event_bytes = max_event_bytes
        self._closed_ttl_seconds = closed_ttl_seconds
        self._transaction_retries = transaction_retries

    async def append(self, tenant_id: str, event: RunStreamEvent) -> str:
        body = canonical_model_bytes(event)
        if len(body) > self._max_event_bytes:
            raise RunStreamError("encoded Run Stream event exceeds the configured size limit")
        stream_key, metadata_key = _keys(tenant_id, event.run_id)
        for _ in range(self._transaction_retries):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(stream_key, metadata_key)
                    await _require_identity(pipeline, metadata_key, tenant_id=tenant_id, run_id=event.run_id)
                    entries = _entry_rows(await pipeline.xrange(stream_key, min=b"-", max=b"+"))
                    duplicate = _find_event(entries, event.event_id, body)
                    if duplicate is not None:
                        return duplicate
                    if _as_text(await pipeline.hget(metadata_key, b"closed_at")) is not None:
                        raise RunStreamClosed("Run Stream is already closed")
                    length = int(await pipeline.xlen(stream_key))
                    pipeline.multi()
                    pipeline.hset(
                        metadata_key,
                        mapping={b"tenant_id": tenant_id.encode(), b"run_id": event.run_id.encode()},
                    )
                    pipeline.xadd(
                        stream_key,
                        {b"event_id": event.event_id.encode(), b"body": body},
                        maxlen=self._max_events,
                        approximate=False,
                    )
                    if length >= self._max_events:
                        pipeline.hset(metadata_key, b"trimmed", b"1")
                    pipeline.persist(stream_key)
                    pipeline.persist(metadata_key)
                    results = await pipeline.execute()
                    return _required_text(results[1], field="Redis Stream entry ID")
                except WatchError:
                    continue
        raise RunStreamError("Run Stream append contention exceeded the retry limit")

    async def close(self, tenant_id: str, run_id: str, *, closed_at: datetime) -> None:
        closed_value = _utc(closed_at).isoformat()
        stream_key, metadata_key = _keys(tenant_id, run_id)
        for _ in range(self._transaction_retries):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(stream_key, metadata_key)
                    await _require_identity(pipeline, metadata_key, tenant_id=tenant_id, run_id=run_id)
                    existing = _as_text(await pipeline.hget(metadata_key, b"closed_at"))
                    if existing is not None:
                        if existing != closed_value:
                            raise RunStreamError("Run Stream terminal boundary changed")
                        return
                    pipeline.multi()
                    pipeline.hset(
                        metadata_key,
                        mapping={
                            b"tenant_id": tenant_id.encode(),
                            b"run_id": run_id.encode(),
                            b"closed_at": closed_value.encode(),
                        },
                    )
                    pipeline.expire(stream_key, self._closed_ttl_seconds)
                    pipeline.expire(metadata_key, self._closed_ttl_seconds)
                    await pipeline.execute()
                    return
                except WatchError:
                    continue
        raise RunStreamError("Run Stream close contention exceeded the retry limit")

    async def mark_incomplete(self, tenant_id: str, run_id: str) -> None:
        """Permanently record that a live publisher lost at least one source event."""

        stream_key, metadata_key = _keys(tenant_id, run_id)
        for _ in range(self._transaction_retries):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(metadata_key)
                    await _require_identity(pipeline, metadata_key, tenant_id=tenant_id, run_id=run_id)
                    closed = _as_text(await pipeline.hget(metadata_key, b"closed_at")) is not None
                    pipeline.multi()
                    pipeline.hset(
                        metadata_key,
                        mapping={
                            b"tenant_id": tenant_id.encode(),
                            b"run_id": run_id.encode(),
                            b"incomplete": b"1",
                        },
                    )
                    if not closed:
                        pipeline.persist(stream_key)
                        pipeline.persist(metadata_key)
                    await pipeline.execute()
                    return
                except WatchError:
                    continue
        raise RunStreamError("Run Stream incomplete marker contention exceeded the retry limit")

    async def complete_attempt_projection(
        self,
        tenant_id: str,
        run_id: str,
        *,
        run_attempt_id: str,
        harness_run_id: str,
    ) -> None:
        """Certify that one Attempt's bounded live publisher drained without loss."""

        stream_key, metadata_key = _keys(tenant_id, run_id)
        field = _attempt_projection_field(run_attempt_id)
        for _ in range(self._transaction_retries):
            async with self._redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(metadata_key)
                    await _require_identity(pipeline, metadata_key, tenant_id=tenant_id, run_id=run_id)
                    existing = _as_text(await pipeline.hget(metadata_key, field))
                    if existing is not None and existing != harness_run_id:
                        raise RunStreamError("RunAttempt live projection identity changed")
                    closed = _as_text(await pipeline.hget(metadata_key, b"closed_at")) is not None
                    pipeline.multi()
                    pipeline.hset(
                        metadata_key,
                        mapping={
                            b"tenant_id": tenant_id.encode(),
                            b"run_id": run_id.encode(),
                            field: harness_run_id.encode(),
                        },
                    )
                    if not closed:
                        pipeline.persist(stream_key)
                        pipeline.persist(metadata_key)
                    await pipeline.execute()
                    return
                except WatchError:
                    continue
        raise RunStreamError("RunAttempt live projection marker contention exceeded the retry limit")

    async def read(
        self,
        tenant_id: str,
        run_id: str,
        *,
        after_stream_id: str | None,
        limit: int,
    ) -> RunStreamPage:
        if limit < 1 or limit > 1000:
            raise ValueError("Run Stream read limit must be between 1 and 1000")
        if after_stream_id is not None:
            _parse_stream_id(after_stream_id)
        stream_key, metadata_key = _keys(tenant_id, run_id)
        minimum = b"-" if after_stream_id is None else f"({after_stream_id}".encode()
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.hgetall(metadata_key)
            pipeline.xrange(stream_key, min=b"-", max=b"+", count=1)
            pipeline.xrevrange(stream_key, max=b"+", min=b"-", count=1)
            pipeline.xrange(stream_key, min=minimum, max=b"+", count=limit)
            metadata_value, boundary_value, tail_value, page_value = await pipeline.execute()
        metadata = _metadata(metadata_value)
        _validate_identity(metadata, tenant_id=tenant_id, run_id=run_id)
        boundary_rows = _entry_rows(boundary_value)
        tail_rows = _entry_rows(tail_value)
        floor = None if not boundary_rows else boundary_rows[0][0]
        high = None if not tail_rows else tail_rows[0][0]
        trimmed = metadata.get("trimmed") == "1"
        if metadata.get("incomplete") == "1" or (
            trimmed and floor is not None and _precedes(after_stream_id or _INITIAL_STREAM_ID, floor)
        ):
            raise RunStreamReplayGap(retained_floor=floor, high_watermark=high)
        rows = _entry_rows(page_value)
        entries = tuple(_decode_entry(row, expected_run_id=run_id) for row in rows)
        return RunStreamPage(
            items=entries,
            next_stream_id=None if not entries else entries[-1].stream_id,
            retained_floor=floor,
            high_watermark=high,
            closed="closed_at" in metadata,
            trimmed=trimmed,
        )

    async def complete_source(self, tenant_id: str, run_id: str) -> CompleteRunStream:
        stream_key, metadata_key = _keys(tenant_id, run_id)
        metadata = _metadata(await self._redis.hgetall(metadata_key))
        _validate_identity(metadata, tenant_id=tenant_id, run_id=run_id)
        closed_at = metadata.get("closed_at")
        if closed_at is None or metadata.get("trimmed") == "1" or metadata.get("incomplete") == "1":
            raise RetainedReplayUnavailable("Run Stream is open, incomplete, or its prefix was trimmed")
        rows = _entry_rows(await self._redis.xrange(stream_key, min=b"-", max=b"+"))
        if not rows or len(rows) > self._max_events:
            raise RetainedReplayUnavailable("Run Stream is empty or exceeds its retained event bound")
        try:
            closed = _utc(datetime.fromisoformat(closed_at))
        except ValueError as error:
            raise RunStreamError("Run Stream close metadata is invalid") from error
        entries = tuple(_decode_entry(row, expected_run_id=run_id) for row in rows)
        _require_attempt_projections(metadata, entries)
        return CompleteRunStream(
            entries=entries,
            closed_at=closed,
            stream_key_digest_sha256=hashlib.sha256(stream_key).hexdigest(),
        )

    async def untrimmed_entries(self, tenant_id: str, run_id: str) -> tuple[RunStreamEntry, ...]:
        """Return the complete retained prefix used to seal Item projections."""

        stream_key, metadata_key = _keys(tenant_id, run_id)
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.hgetall(metadata_key)
            pipeline.xrange(stream_key, min=b"-", max=b"+")
            metadata_value, rows_value = await pipeline.execute()
        metadata = _metadata(metadata_value)
        _validate_identity(metadata, tenant_id=tenant_id, run_id=run_id)
        if metadata.get("trimmed") == "1" or metadata.get("incomplete") == "1":
            raise RetainedReplayUnavailable("Run Stream prefix is incomplete or was trimmed")
        rows = _entry_rows(rows_value)
        return tuple(_decode_entry(row, expected_run_id=run_id) for row in rows)


def _keys(tenant_id: str, run_id: str) -> tuple[bytes, bytes]:
    locator = hashlib.sha256(f"{tenant_id}\0{run_id}".encode()).hexdigest()
    slot = f"{{{locator}}}"
    return f"a13n:run-stream:{slot}:events".encode(), f"a13n:run-stream:{slot}:metadata".encode()


def run_stream_key_digest_sha256(tenant_id: str, run_id: str) -> str:
    stream_key, _ = _keys(tenant_id, run_id)
    return hashlib.sha256(stream_key).hexdigest()


def _attempt_projection_field(run_attempt_id: str) -> bytes:
    return f"attempt_projection:{run_attempt_id}".encode()


def _require_attempt_projections(
    metadata: Mapping[str, str],
    entries: tuple[RunStreamEntry, ...],
) -> None:
    for entry in entries:
        event = entry.event
        if event.event_type != "run_attempt.running":
            continue
        attempt_id = event.run_attempt_id
        harness_run_id = event.harness_run_id
        if not isinstance(attempt_id, str) or not isinstance(harness_run_id, str):
            raise RunStreamError("RunAttempt running projection omitted Harness correlation")
        field = _attempt_projection_field(attempt_id).decode()
        if metadata.get(field) != harness_run_id:
            raise RetainedReplayUnavailable("RunAttempt live presentation projection is incomplete")


async def _require_identity(pipeline: Pipeline, metadata_key: bytes, *, tenant_id: str, run_id: str) -> None:
    values = await pipeline.hmget(metadata_key, b"tenant_id", b"run_id")
    existing_tenant, existing_run = cast(Sequence[bytes | str | None], values)
    if existing_tenant is None and existing_run is None:
        return
    if _as_text(existing_tenant) != tenant_id or _as_text(existing_run) != run_id:
        raise RunStreamError("Run Stream locator resolved to another resource")


def _validate_identity(metadata: Mapping[str, str], *, tenant_id: str, run_id: str) -> None:
    if not metadata:
        return
    if metadata.get("tenant_id") != tenant_id or metadata.get("run_id") != run_id:
        raise RunStreamError("Run Stream metadata belongs to another resource")


def _entry_rows(value: object) -> tuple[tuple[str, Mapping[bytes, bytes]], ...]:
    rows = cast(Sequence[tuple[bytes | str, Mapping[bytes | str, bytes | str]]], value)
    return tuple(
        (
            _required_text(stream_id, field="Redis Stream entry ID"),
            {_as_bytes(key): _as_bytes(item) for key, item in fields.items()},
        )
        for stream_id, fields in rows
    )


def _find_event(
    rows: Sequence[tuple[str, Mapping[bytes, bytes]]],
    event_id: str,
    body: bytes,
) -> str | None:
    expected = event_id.encode()
    for stream_id, fields in rows:
        if fields.get(b"event_id") == expected:
            if fields.get(b"body") != body:
                raise RunStreamError("Run Stream event identity was reused with different content")
            return stream_id
    return None


def _decode_entry(row: tuple[str, Mapping[bytes, bytes]], *, expected_run_id: str) -> RunStreamEntry:
    stream_id, fields = row
    body = fields.get(b"body")
    event_id = fields.get(b"event_id")
    if body is None or event_id is None:
        raise RunStreamError("Redis Stream entry omitted required fields")
    try:
        event = decode_canonical_model(body, _EVENT_ADAPTER)
    except DurableObjectCodecError as error:
        raise RunStreamError("Redis Stream entry body is invalid") from error
    if event.run_id != expected_run_id or event.event_id.encode() != event_id:
        raise RunStreamError("Redis Stream entry correlation is invalid")
    return RunStreamEntry(stream_id=stream_id, event=event)


def _metadata(value: object) -> dict[str, str]:
    fields = cast(Mapping[bytes | str, bytes | str], value)
    return {
        _required_text(key, field="metadata key"): _required_text(item, field="metadata value")
        for key, item in fields.items()
    }


def _precedes(left: str, right: str) -> bool:
    return _parse_stream_id(left) < _parse_stream_id(right)


def _parse_stream_id(value: str) -> tuple[int, int]:
    try:
        milliseconds, sequence = value.split("-", maxsplit=1)
        parsed = int(milliseconds), int(sequence)
    except (ValueError, AttributeError) as error:
        raise ValueError("Run Stream cursor is invalid") from error
    if min(parsed) < 0 or f"{parsed[0]}-{parsed[1]}" != value:
        raise ValueError("Run Stream cursor is invalid")
    return parsed


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Run Stream timestamp must include a UTC offset")
    return value.astimezone(UTC)


def _as_text(value: object, *, field: str = "value") -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError as error:
            raise RunStreamError(f"Redis returned invalid UTF-8 for {field}") from error
    if isinstance(value, str):
        return value
    raise RunStreamError(f"Redis returned an invalid {field}")


def _required_text(value: object, *, field: str) -> str:
    decoded = _as_text(value, field=field)
    if decoded is None:
        raise RunStreamError(f"Redis omitted {field}")
    return decoded


def _as_bytes(value: bytes | str) -> bytes:
    return value if isinstance(value, bytes) else value.encode()


__all__ = ["RedisRunStream", "run_stream_key_digest_sha256"]
