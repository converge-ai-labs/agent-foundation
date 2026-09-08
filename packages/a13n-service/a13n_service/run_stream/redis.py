"""Bounded Redis presentation with atomic activation and Attempt publication."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from importlib.resources import files
from typing import cast

import rfc8785
from pydantic import JsonValue, TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from a13n_service.storage.codec import DurableObjectCodecError, canonical_model_bytes, decode_canonical_model
from a13n_service.storage.redis import redis_memory_identity
from a13n_service.temporal import require_aware_utc

from .domain import (
    ActivationResult,
    CompleteRunStream,
    PublicationRejected,
    PublicationUnavailable,
    RecoveryPayload,
    RecoveryReason,
    RetainedReplayUnavailable,
    RunStreamClosed,
    RunStreamEntry,
    RunStreamError,
    RunStreamEvent,
    RunStreamPage,
    RunStreamReplayGap,
    deterministic_run_stream_event_id,
)

type _StreamRows = tuple[tuple[str, Mapping[bytes, bytes]], ...]

_EVENT_ADAPTER = TypeAdapter(RunStreamEvent)
_INITIAL_STREAM_ID = "0-0"
_SCRIPT = files(__package__).joinpath("publication.lua").read_text()


class RedisRunStream:
    """One Run Stream; trusted lifecycle projection and fenced live writes are distinct."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_events: int = 4096,
        max_event_bytes: int = 320 * 1024,
        closed_ttl_seconds: int = 24 * 60 * 60,
    ) -> None:
        if min(max_events, max_event_bytes, closed_ttl_seconds) < 1:
            raise ValueError("Run Stream bounds must be positive")
        self._redis = redis
        self._script = redis.register_script(_SCRIPT)
        self._memory_server_id = redis_memory_identity(redis)
        self._max_events = max_events
        self._max_event_bytes = max_event_bytes
        self._closed_ttl_seconds = closed_ttl_seconds

    async def server_incarnation(self) -> str:
        """Capture continuity before reading durable bootstrap authority."""
        if self._memory_server_id:
            return self._memory_server_id
        try:
            info = await self._redis.info("server")
            return _required_text(info.get("run_id"), field="Redis primary incarnation")
        except RedisError as error:
            raise PublicationUnavailable("Redis primary incarnation is unavailable") from error

    async def initialize(
        self,
        organization_id: str,
        accepted: RunStreamEvent,
        *,
        allow_create: bool,
        expected_server_id: str,
    ) -> str:
        """Project the committed accepted fact before any publisher can activate."""
        if accepted.event_type != "run.accepted" or accepted.lifecycle_event_id is None:
            raise ValueError("Run Stream initialization requires a committed accepted fact")
        result = await self._mutate(
            organization_id,
            accepted.run_id,
            "initialize",
            events=(accepted,),
            allow_create=allow_create,
            expected_server_id=expected_server_id,
        )
        return _required_text(result[0], field="Redis Stream entry ID")

    async def activate(
        self,
        organization_id: str,
        leased: RunStreamEvent,
        *,
        attempt_number: int,
        reason: RecoveryReason | None,
        allow_create: bool,
    ) -> ActivationResult:
        events = _activation_events(leased, attempt_number=attempt_number, reason=reason)
        result = await self._mutate(
            organization_id,
            leased.run_id,
            "activate",
            events=events,
            attempt_id=leased.run_attempt_id,
            fence=str(attempt_number),
            allow_create=allow_create,
        )
        return _activation_result(result)

    async def activation_result(
        self,
        organization_id: str,
        leased: RunStreamEvent,
        *,
        attempt_number: int,
        reason: RecoveryReason | None,
    ) -> ActivationResult | None:
        events = _activation_events(leased, attempt_number=attempt_number, reason=reason)
        result = await self._mutate(
            organization_id,
            leased.run_id,
            "inspect",
            events=events,
            attempt_id=leased.run_attempt_id,
            fence=str(attempt_number),
        )
        return _activation_result(result) if result else None

    async def append(self, organization_id: str, event: RunStreamEvent, *, attempt_number: int) -> str:
        """Atomically check the exact active Attempt and append its observation."""
        if event.run_attempt_id is None or attempt_number < 1:
            raise ValueError("Attempt publication requires its identity and positive fencing number")
        if event.lifecycle_event_id is not None or event.event_type.startswith(("run.", "run_attempt.")):
            raise ValueError("Attempt writers cannot publish lifecycle or activation events")
        result = await self._mutate(
            organization_id,
            event.run_id,
            "append",
            events=(event,),
            attempt_owned=True,
            attempt_id=event.run_attempt_id,
            fence=str(attempt_number),
        )
        return _required_text(result[0], field="Redis Stream entry ID")

    async def append_lifecycle(self, organization_id: str, event: RunStreamEvent) -> str:
        """Trusted historical facts never advance the publication generation."""
        if (
            event.lifecycle_event_id is None
            or event.event_type in {"run.accepted", "run.recovery", "run_attempt.leased"}
            or not (event.event_type.startswith(("run.", "run_attempt.")) or event.event_type == "item.interrupted")
        ):
            raise ValueError("Lifecycle projection requires a committed fact outside activation")
        result = await self._mutate(organization_id, event.run_id, "lifecycle", events=(event,))
        return _required_text(result[0], field="Redis Stream entry ID")

    async def close(self, organization_id: str, run_id: str, *, closed_at: datetime) -> None:
        """Trusted terminal lifecycle projection closes a Run, never an Attempt writer."""
        await self._mutate(organization_id, run_id, "close", closed_at=_utc(closed_at).isoformat())

    async def mark_incomplete(
        self,
        organization_id: str,
        run_id: str,
        *,
        run_attempt_id: str,
        attempt_number: int,
    ) -> None:
        """An active Attempt may report its own loss, but cannot damage a successor."""
        await self._mutate(
            organization_id,
            run_id,
            "incomplete",
            attempt_owned=True,
            attempt_id=run_attempt_id,
            fence=str(attempt_number),
        )

    async def mark_lifecycle_incomplete(self, organization_id: str, run_id: str) -> None:
        """Trusted projection records missing committed history independently of an Attempt."""
        await self._mutate(organization_id, run_id, "incomplete")

    async def complete_attempt_projection(
        self,
        organization_id: str,
        run_id: str,
        *,
        run_attempt_id: str,
        attempt_number: int,
        harness_run_id: str,
    ) -> None:
        await self._mutate(
            organization_id,
            run_id,
            "complete",
            attempt_owned=True,
            attempt_id=run_attempt_id,
            fence=str(attempt_number),
            harness_run_id=harness_run_id,
        )

    async def _mutate(
        self,
        organization_id: str,
        run_id: str,
        operation: str,
        *,
        events: tuple[RunStreamEvent, ...] = (),
        **fields: JsonValue,
    ) -> Sequence[object]:
        encoded = []
        for event in events:
            body = canonical_model_bytes(event)
            if len(body) > self._max_event_bytes:
                raise RunStreamError("encoded Run Stream event exceeds the configured size limit")
            encoded.append({"id": event.event_id, "body": body.decode(), "digest": hashlib.sha256(body).hexdigest()})
        values = {
            "operation": "activate" if operation == "inspect" else operation,
            "organization_id": organization_id,
            "run_id": run_id,
            "events": encoded,
            **fields,
        }
        # Read-only inspection and retries have the identical activation identity.
        identity = {key: value for key, value in values.items() if key not in {"allow_create", "expected_server_id"}}
        digest = hashlib.sha256(rfc8785.dumps(identity)).hexdigest()
        request = {
            **values,
            "operation": operation,
            "digest": digest,
            "memory_server_id": self._memory_server_id,
            "max_events": self._max_events,
            "max_event_bytes": self._max_event_bytes,
            "closed_ttl_seconds": self._closed_ttl_seconds,
        }
        try:
            return cast(
                Sequence[object],
                await self._script(keys=list(_keys(organization_id, run_id)), args=[rfc8785.dumps(request)]),
            )
        except ResponseError as error:
            message = str(error)
            if "RUN_STREAM_STALE" in message:
                raise PublicationRejected("RunAttempt publication generation is no longer active") from error
            if "RUN_STREAM_CLOSED" in message:
                raise RunStreamClosed("Run Stream is already closed") from error
            if "RUN_STREAM_CONFLICT" in message:
                raise RunStreamError("Run Stream event identity was reused with different content") from error
            if "RUN_STREAM_PROJECTION_IDENTITY" in message:
                raise RunStreamError("RunAttempt live projection identity changed") from error
            if "RUN_STREAM_CLOSE_CONFLICT" in message:
                raise RunStreamError("Run Stream terminal boundary changed") from error
            raise PublicationUnavailable("Run Stream activation or continuity is unavailable") from error
        except RedisError as error:
            raise PublicationUnavailable("Run Stream publication outcome is unconfirmed") from error

    async def read(
        self,
        organization_id: str,
        run_id: str,
        *,
        after_stream_id: str | None,
        limit: int,
    ) -> RunStreamPage:
        if limit < 1 or limit > 1000:
            raise ValueError("Run Stream read limit must be between 1 and 1000")
        return await self._read(organization_id, run_id, after_stream_id=after_stream_id, limit=limit)

    async def _read(
        self,
        organization_id: str,
        run_id: str,
        *,
        after_stream_id: str | None,
        limit: int,
    ) -> RunStreamPage:
        metadata, boundary_rows, tail_rows, rows = await self._read_rows(
            organization_id, run_id, after_stream_id=after_stream_id, limit=limit
        )
        floor = None if not boundary_rows else boundary_rows[0][0]
        high = None if not tail_rows else tail_rows[0][0]
        trimmed = metadata.get("trimmed") == "1"
        if metadata.get("incomplete") == "1" or (
            trimmed and floor is not None and _precedes(after_stream_id or _INITIAL_STREAM_ID, floor)
        ):
            raise RunStreamReplayGap(retained_floor=floor, high_watermark=high)
        entries = tuple(_decode_entry(row, expected_run_id=run_id) for row in rows)
        return RunStreamPage(
            items=entries,
            next_stream_id=None if not entries else entries[-1].stream_id,
            retained_floor=floor,
            high_watermark=high,
            closed="closed_at" in metadata,
            trimmed=trimmed,
        )

    async def _read_rows(
        self,
        organization_id: str,
        run_id: str,
        *,
        after_stream_id: str | None,
        limit: int,
    ) -> tuple[dict[str, str], _StreamRows, _StreamRows, _StreamRows]:
        if after_stream_id is not None:
            _parse_stream_id(after_stream_id)
        try:
            values = await self._mutate(organization_id, run_id, "read", after=after_stream_id or "", limit=limit)
        except PublicationUnavailable as error:
            raise RunStreamReplayGap(retained_floor=None, high_watermark=None) from error
        metadata = _metadata(_field_map(values[0]))
        _validate_identity(metadata, organization_id=organization_id, run_id=run_id)
        return metadata, _script_rows(values[1]), _script_rows(values[2]), _script_rows(values[3])

    async def complete_source(self, organization_id: str, run_id: str) -> CompleteRunStream:
        try:
            metadata, _, _, rows = await self._read_rows(
                organization_id, run_id, after_stream_id=None, limit=self._max_events + 1
            )
        except RunStreamReplayGap as error:
            raise RetainedReplayUnavailable("Run Stream continuity is unavailable") from error
        closed_at = metadata.get("closed_at")
        if closed_at is None or metadata.get("trimmed") == "1" or metadata.get("incomplete") == "1":
            raise RetainedReplayUnavailable("Run Stream is open, incomplete, or its prefix was trimmed")
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
            stream_key_digest_sha256=run_stream_key_digest_sha256(organization_id, run_id),
        )

    async def untrimmed_entries(self, organization_id: str, run_id: str) -> tuple[RunStreamEntry, ...]:
        try:
            page = await self._read(organization_id, run_id, after_stream_id=None, limit=self._max_events)
        except RunStreamReplayGap as error:
            raise RetainedReplayUnavailable("Run Stream prefix is incomplete or was trimmed") from error
        return page.items


def _activation_events(
    leased: RunStreamEvent,
    *,
    attempt_number: int,
    reason: RecoveryReason | None,
) -> tuple[RunStreamEvent, ...]:
    if leased.event_type != "run_attempt.leased" or leased.run_attempt_id is None or leased.lifecycle_event_id is None:
        raise ValueError("Publication activation requires the committed leased fact")
    if attempt_number < 1 or (attempt_number == 1) != (reason is None):
        raise ValueError("Only successor activation has a recovery reason")
    data = leased.payload.get("data")
    if not isinstance(data, dict) or data.get("attempt_number") != attempt_number:
        raise ValueError("Publication fencing number differs from the committed leased fact")
    if reason is None:
        return (leased,)
    RecoveryPayload(reason=reason)
    recovery = RunStreamEvent(
        event_id=deterministic_run_stream_event_id("run.recovery", leased.lifecycle_event_id),
        event_type="run.recovery",
        run_id=leased.run_id,
        thread_id=leased.thread_id,
        run_attempt_id=leased.run_attempt_id,
        lifecycle_event_id=leased.lifecycle_event_id,
        occurred_at=leased.occurred_at,
        payload={"reason": reason},
    )
    return leased, recovery


def _activation_result(values: Sequence[object]) -> ActivationResult:
    return ActivationResult(
        leased_stream_id=_required_text(values[0], field="leased cursor"),
        recovery_stream_id=_required_text(values[1], field="recovery cursor") or None,
        active=_required_text(values[2], field="active generation") == "1",
    )


def _field_map(value: object) -> Mapping[bytes, bytes]:
    values = cast(Sequence[bytes], value)
    return dict(zip(values[::2], values[1::2], strict=True))


def _script_rows(value: object) -> tuple[tuple[str, Mapping[bytes, bytes]], ...]:
    return tuple(
        (_required_text(row[0], field="Redis Stream entry ID"), _field_map(row[1]))
        for row in cast(Sequence[Sequence[object]], value)
    )


def _keys(organization_id: str, run_id: str) -> tuple[bytes, bytes]:
    locator = hashlib.sha256(f"{organization_id}\0{run_id}".encode()).hexdigest()
    slot = f"{{{locator}}}"
    return f"a13n:run-stream:{slot}:events".encode(), f"a13n:run-stream:{slot}:metadata".encode()


def run_stream_key_digest_sha256(organization_id: str, run_id: str) -> str:
    stream_key, _ = _keys(organization_id, run_id)
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


def _validate_identity(metadata: Mapping[str, str], *, organization_id: str, run_id: str) -> None:
    if not metadata:
        return
    if metadata.get("organization_id") != organization_id or metadata.get("run_id") != run_id:
        raise RunStreamError("Run Stream metadata belongs to another resource")


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
    try:
        return require_aware_utc(value)
    except ValueError as error:
        raise ValueError("Run Stream timestamp must include a UTC offset") from error


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


__all__ = ["RedisRunStream", "run_stream_key_digest_sha256"]
