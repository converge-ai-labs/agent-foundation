"""Publish complete sealed Run Streams as immutable retained replay snapshots."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import rfc8785
from pydantic import JsonValue, TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.codec import DurableObjectCodecError, decode_canonical_model
from a13n_service.interactions.domain import Run, RunAttemptStatus, RunStatus
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunObjectError, RunPayloadStore
from a13n_service.storage import short_session

from .domain import (
    RetainedRunStreamEvent,
    RunOutputItemContent,
    RunReplaySnapshot,
    RunStreamEvent,
    run_stream_key,
    run_stream_key_digest,
)
from .items import RetainedItemProjectionError, build_retained_items
from .replay_store import RunReplayError, RunReplayStore, RunReplayUnavailable
from .stream import (
    DEFAULT_MAX_RUN_STREAM_EVENT_BYTES,
    RUN_STREAM_FIELD,
    RUN_STREAM_OPEN_EVENT_TYPE,
    RUN_STREAM_OPEN_ID,
)

DEFAULT_MAX_REPLAY_EVENTS = 10_000
DEFAULT_MAX_REPLAY_ITEMS = 2_048
DEFAULT_MAX_REPLAY_EVENT_BYTES = 16 * 1024 * 1024
_STREAM_EVENT_ADAPTER = TypeAdapter(RunStreamEvent)
_TERMINAL_EVENT_BY_STATUS = {
    RunStatus.completed: "RUN_FINISHED",
    RunStatus.failed: "RUN_ERROR",
    RunStatus.cancelled: "RUN_ERROR",
}
_TERMINAL_ATTEMPT_STATUS_BY_RUN_STATUS = {
    RunStatus.completed: RunAttemptStatus.succeeded,
    RunStatus.failed: RunAttemptStatus.failed,
    RunStatus.cancelled: RunAttemptStatus.cancelled,
}


class RunReplayPublisher:
    """Compact one complete retained Redis horizon after its Run seals."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        replays: RunReplayStore,
        payloads: RunPayloadStore,
        *,
        stream_ttl_seconds: int,
        max_events: int = DEFAULT_MAX_REPLAY_EVENTS,
        max_items: int = DEFAULT_MAX_REPLAY_ITEMS,
        max_event_bytes: int = DEFAULT_MAX_RUN_STREAM_EVENT_BYTES,
        max_total_event_bytes: int = DEFAULT_MAX_REPLAY_EVENT_BYTES,
    ) -> None:
        if stream_ttl_seconds < 1:
            raise ValueError("sealed Run Stream TTL must be positive")
        if max_events < 2 or max_items < 1 or max_event_bytes < 1 or max_total_event_bytes < 1:
            raise ValueError("Run replay publication bounds must be positive")
        self._sessions = sessions
        self._redis = redis
        self._replays = replays
        self._payloads = payloads
        self._stream_ttl_seconds = stream_ttl_seconds
        self._max_events = max_events
        self._max_items = max_items
        self._max_event_bytes = max_event_bytes
        self._max_total_event_bytes = max_total_event_bytes

    async def publish(self, *, tenant_id: str, run_id: str) -> RunReplaySnapshot:
        try:
            existing = await self._replays.read(tenant_id, run_id)
        except RunReplayUnavailable:
            pass
        else:
            await self._expire_stream(tenant_id, run_id)
            return existing
        run = await self._read_sealed_run(tenant_id, run_id)
        events = await self._read_complete_stream(run)
        await self._verify_attempt_provenance(run, events)
        terminal_output, selected_output = await self._terminal_output(run)
        self._verify_terminal_observation(run, events[-1].event, selected_output)
        try:
            items = build_retained_items(events, terminal_output=terminal_output)
        except RetainedItemProjectionError as error:
            raise RunReplayUnavailable("Run Stream Items are incomplete") from error
        if len(items) > self._max_items:
            raise RunReplayUnavailable("Run Stream exceeds the configured Item count")
        sealed_at = run.sealed_at
        if sealed_at is None:
            raise RunReplayUnavailable("Run is not sealed")
        snapshot = RunReplaySnapshot(
            run_id=run.id,
            thread_id=run.thread_id,
            stream_key_digest_sha256=run_stream_key_digest(tenant_id, run.id),
            first_stream_id=events[0].stream_id,
            last_stream_id=events[-1].stream_id,
            closed_at=sealed_at,
            source_run_attempt_ids=tuple(
                dict.fromkeys(event.event.run_attempt_id for event in events if event.event.run_attempt_id is not None)
            ),
            events=events,
            items=items,
        )
        published = await self._replays.create(tenant_id, snapshot)
        await self._expire_stream(tenant_id, run_id)
        return published

    async def _read_sealed_run(self, tenant_id: str, run_id: str) -> Run:
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id)
            )
            if record is None:
                raise RunReplayUnavailable("Run is unavailable")
            run = record.to_resource()
        if run.status not in _TERMINAL_EVENT_BY_STATUS:
            raise RunReplayUnavailable("Run has not sealed a terminal presentation")
        return run

    async def _read_complete_stream(self, run: Run) -> tuple[RetainedRunStreamEvent, ...]:
        key = run_stream_key(run.tenant_id, run.id)
        retained: list[RetainedRunStreamEvent] = []
        cursor: str | bytes = b"-"
        total_bytes = 0
        try:
            while True:
                batch = await self._redis.xrange(
                    key,
                    min=cursor,
                    max=b"+",
                    count=min(512, self._max_events + 1 - len(retained)),
                )
                if not batch:
                    break
                for stream_id, fields in batch:
                    if fields is None:
                        raise RunReplayUnavailable("Run Stream entry has no field set")
                    body = _event_body(fields)
                    if len(body) > self._max_event_bytes:
                        raise RunReplayUnavailable("Run Stream event exceeds its configured limit")
                    total_bytes += len(body)
                    if total_bytes > self._max_total_event_bytes:
                        raise RunReplayUnavailable("Run Stream exceeds its configured payload limit")
                    try:
                        event = decode_canonical_model(body, _STREAM_EVENT_ADAPTER)
                    except DurableObjectCodecError as error:
                        raise RunReplayUnavailable("Run Stream contains an invalid event") from error
                    retained.append(
                        RetainedRunStreamEvent(
                            stream_id=_stream_id(stream_id),
                            event=event,
                        )
                    )
                    if len(retained) > self._max_events:
                        raise RunReplayUnavailable("Run Stream exceeds its configured event count")
                cursor = f"({_stream_id(batch[-1][0])}"
        except RedisError as error:
            raise RunReplayError("Run Stream could not be read") from error
        self._verify_stream_shape(run, retained)
        return tuple(retained)

    def _verify_stream_shape(self, run: Run, events: Sequence[RetainedRunStreamEvent]) -> None:
        if not events or events[0].stream_id != RUN_STREAM_OPEN_ID:
            raise RunReplayUnavailable("Run Stream opening marker is outside the retained horizon")
        opening = events[0].event
        if opening.event_type != RUN_STREAM_OPEN_EVENT_TYPE or opening.item_id is not None:
            raise RunReplayUnavailable("Run Stream opening marker is invalid")
        if any(event.event.run_id != run.id or event.event.thread_id != run.thread_id for event in events):
            raise RunReplayUnavailable("Run Stream identity does not match its sealed Run")
        expected_terminal = _TERMINAL_EVENT_BY_STATUS[run.status]
        if events[-1].event.event_type != expected_terminal:
            raise RunReplayUnavailable("Run Stream does not end with the sealed Run outcome")

    async def _verify_attempt_provenance(
        self,
        run: Run,
        events: Sequence[RetainedRunStreamEvent],
    ) -> None:
        attempt_ids = {event.event.run_attempt_id for event in events if event.event.run_attempt_id is not None}
        if not attempt_ids:
            raise RunReplayUnavailable("Run Stream has no RunAttempt provenance")
        async with short_session(self._sessions) as database:
            attempts = tuple(
                (
                    await database.scalars(
                        select(RunAttemptRecord).where(
                            RunAttemptRecord.tenant_id == run.tenant_id,
                            RunAttemptRecord.run_id == run.id,
                            RunAttemptRecord.id.in_(attempt_ids),
                        )
                    )
                ).all()
            )
        by_id = {attempt.id: attempt for attempt in attempts}
        if set(by_id) != attempt_ids:
            raise RunReplayUnavailable("Run Stream RunAttempt provenance is incomplete")
        for retained in events:
            event = retained.event
            attempt = by_id.get(event.run_attempt_id) if event.run_attempt_id is not None else None
            if attempt is None or event.harness_run_id is None or attempt.harness_run_id != event.harness_run_id:
                raise RunReplayUnavailable("Run Stream Harness provenance contradicts its RunAttempt")
        terminal_attempt_id = events[-1].event.run_attempt_id
        terminal_attempt = by_id.get(terminal_attempt_id) if terminal_attempt_id is not None else None
        expected_status = _TERMINAL_ATTEMPT_STATUS_BY_RUN_STATUS[run.status]
        if terminal_attempt is None or terminal_attempt.status != expected_status.value:
            raise RunReplayUnavailable("terminal Run Stream provenance contradicts its sealed outcome")

    async def _terminal_output(self, run: Run) -> tuple[RunOutputItemContent | None, JsonValue]:
        if run.status is not RunStatus.completed:
            return None, None
        if run.output_object is None:
            value = run.output
            digest = hashlib.sha256(rfc8785.dumps(value)).hexdigest()
            return RunOutputItemContent(result_digest=digest, output=value), value
        try:
            envelope = await self._payloads.verify_reference(
                run.tenant_id,
                run.id,
                "output",
                run.output_object,
            )
        except RunObjectError as error:
            raise RunReplayUnavailable("selected Run output object is unavailable") from error
        digest = hashlib.sha256(rfc8785.dumps(envelope.payload)).hexdigest()
        return (
            RunOutputItemContent(
                result_digest=digest,
                output_object=run.output_object,
            ),
            envelope.payload,
        )

    def _verify_terminal_observation(
        self,
        run: Run,
        event: RunStreamEvent,
        selected_output: JsonValue,
    ) -> None:
        raw_event = event.payload.get("rawEvent")
        if not isinstance(raw_event, dict) or raw_event.get("status") != run.status.value:
            raise RunReplayUnavailable("terminal Run Stream status contradicts its sealed Run")
        if run.status is RunStatus.completed:
            omitted = raw_event.get("result_omitted") is True
            observed_output = event.payload.get("result")
            if omitted:
                if observed_output is not None:
                    raise RunReplayUnavailable("omitted terminal output still carries content")
            elif "result" not in event.payload or observed_output != selected_output:
                raise RunReplayUnavailable("terminal Run Stream output contradicts its sealed Run")
            return
        if run.status is RunStatus.cancelled:
            if event.payload.get("code") != "run_cancelled":
                raise RunReplayUnavailable("terminal cancellation presentation is invalid")
            return
        failure = run.failure
        if (
            failure is None
            or event.payload.get("code") != failure.code
            or event.payload.get("message") != failure.message
        ):
            raise RunReplayUnavailable("terminal failure presentation contradicts its sealed Run")

    async def _expire_stream(self, tenant_id: str, run_id: str) -> None:
        try:
            await self._redis.expire(
                run_stream_key(tenant_id, run_id),
                self._stream_ttl_seconds,
            )
        except RedisError as error:
            raise RunReplayError("sealed Run Stream retention could not be applied") from error


def _event_body(fields: dict[bytes | str, bytes | str]) -> bytes:
    if len(fields) != 1:
        raise RunReplayUnavailable("Run Stream entry has an invalid field set")
    body = fields.get(RUN_STREAM_FIELD)
    if body is None:
        body = fields.get(RUN_STREAM_FIELD.decode())
    if not isinstance(body, bytes):
        raise RunReplayUnavailable("Run Stream event body is not binary canonical JSON")
    return body


def _stream_id(value: bytes | str | None) -> str:
    if value is None:
        raise RunReplayUnavailable("Run Stream entry has no cursor")
    return value.decode() if isinstance(value, bytes) else value


__all__ = [
    "DEFAULT_MAX_REPLAY_EVENTS",
    "DEFAULT_MAX_REPLAY_EVENT_BYTES",
    "DEFAULT_MAX_REPLAY_ITEMS",
    "RunReplayPublisher",
]
