"""Bounded Agent Stream Protocol projection into one stable Redis Run Stream."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver
from pydantic import JsonValue
from redis.asyncio import Redis

from a13n_service.interactions.codec import canonical_model_bytes

from .domain import RunStreamEvent, run_stream_key

RUN_STREAM_OPEN_ID = "0-1"
RUN_STREAM_FIELD = b"event"
RUN_STREAM_OPEN_EVENT_TYPE = "a13n.run_stream.opened"
DEFAULT_MAX_RUN_STREAM_ENTRIES = 10_000
DEFAULT_MAX_RUN_STREAM_EVENT_BYTES = 256 * 1024

_TEXT_EVENT_TYPES = frozenset(
    {
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_CHUNK",
    }
)
_REASONING_EVENT_TYPES = frozenset(
    {
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_MESSAGE_CHUNK",
        "REASONING_END",
        "REASONING_ENCRYPTED_VALUE",
    }
)
_TOOL_CALL_EVENT_TYPES = frozenset(
    {
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_CHUNK",
    }
)
_ACTIVITY_EVENT_TYPES = frozenset({"ACTIVITY_SNAPSHOT", "ACTIVITY_DELTA"})


class RunStreamProjectionError(RuntimeError):
    """A Harness observation cannot enter the bounded Run Stream safely."""


class RunStreamProjector:
    """Observe one Harness Run and append its bounded public events with backpressure."""

    def __init__(
        self,
        redis: Redis,
        *,
        tenant_id: str,
        run_id: str,
        thread_id: str,
        run_attempt_id: str,
        max_entries: int = DEFAULT_MAX_RUN_STREAM_ENTRIES,
        max_event_bytes: int = DEFAULT_MAX_RUN_STREAM_EVENT_BYTES,
    ) -> None:
        if max_entries < 2:
            raise ValueError("Run Stream must retain its opening marker and at least one event")
        if max_event_bytes < 1:
            raise ValueError("Run Stream event limit must be positive")
        self._redis = redis
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._thread_id = thread_id
        self._run_attempt_id = run_attempt_id
        self._max_entries = max_entries
        self._max_event_bytes = max_event_bytes
        self._observer = HarnessAguiObserver()
        self._harness_run_id: str | None = None
        self._opened = False

    async def project(self, source: HarnessStreamEvent[Any]) -> tuple[RunStreamEvent, ...]:
        """Project and append one exact Harness stream item."""

        if source.thread_id != self._thread_id:
            raise RunStreamProjectionError("Harness observation Thread does not match the Run Stream")
        if self._harness_run_id is not None and source.run_id != self._harness_run_id:
            raise RunStreamProjectionError("Harness observation Run identity changed within one projector")
        observed = self._observer.observe(source)
        projected = tuple(
            self._project_event(source, index=index, event=event.model_dump(mode="json", by_alias=True))
            for index, event in enumerate(observed)
        )
        await self._append(source, projected)
        self._harness_run_id = source.run_id
        return projected

    async def _append(
        self,
        source: HarnessStreamEvent[Any],
        projected: tuple[RunStreamEvent, ...],
    ) -> None:
        key = run_stream_key(self._tenant_id, self._run_id)
        opening: RunStreamEvent | None = None
        if not self._opened:
            first = await self._redis.xrange(key, min=b"-", max=b"+", count=1)
            if not first:
                opening = self._opening_event(source)
            elif _stream_id(first[0][0]) != RUN_STREAM_OPEN_ID:
                raise RunStreamProjectionError("Run Stream opening marker is outside the retained horizon")
        pipeline = self._redis.pipeline(transaction=True)
        if opening is not None:
            pipeline.xadd(
                key,
                {RUN_STREAM_FIELD: canonical_model_bytes(opening)},
                id=RUN_STREAM_OPEN_ID,
                maxlen=self._max_entries,
                approximate=False,
            )
        for event in projected:
            pipeline.xadd(
                key,
                {RUN_STREAM_FIELD: canonical_model_bytes(event)},
                maxlen=self._max_entries,
                approximate=False,
            )
        await pipeline.execute()
        self._opened = True

    def _opening_event(self, source: HarnessStreamEvent[Any]) -> RunStreamEvent:
        opened = RunStreamEvent(
            event_id=_stable_id(
                "rse",
                self._run_attempt_id,
                source.run_id,
                "stream-opened",
            ),
            event_type=RUN_STREAM_OPEN_EVENT_TYPE,
            run_id=self._run_id,
            thread_id=self._thread_id,
            run_attempt_id=self._run_attempt_id,
            harness_run_id=source.run_id,
            occurred_at=source.occurred_at,
            payload={"schema_version": "1"},
        )
        if len(canonical_model_bytes(opened)) > self._max_event_bytes:
            raise RunStreamProjectionError("Run Stream opening marker exceeds the configured event limit")
        return opened

    def _project_event(
        self,
        source: HarnessStreamEvent[Any],
        *,
        index: int,
        event: dict[str, JsonValue],
    ) -> RunStreamEvent:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise RunStreamProjectionError("Agent Stream Protocol event type is missing")
        payload = _canonical_event_payload(event)
        projected = self._new_event(source, index=index, event_type=event_type, payload=payload)
        if len(canonical_model_bytes(projected)) <= self._max_event_bytes:
            return projected
        if event_type != "RUN_FINISHED":
            raise RunStreamProjectionError("Agent Stream Protocol event exceeds the configured event limit")
        projected = self._new_event(
            source,
            index=index,
            event_type=event_type,
            payload=_without_terminal_result(payload),
        )
        if len(canonical_model_bytes(projected)) > self._max_event_bytes:
            raise RunStreamProjectionError("Agent Stream Protocol terminal event exceeds the configured event limit")
        return projected

    def _new_event(
        self,
        source: HarnessStreamEvent[Any],
        *,
        index: int,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> RunStreamEvent:
        event_id = _stable_id(
            "rse",
            self._run_attempt_id,
            source.run_id,
            str(source.sequence),
            str(index),
            hashlib.sha256(rfc8785.dumps(payload)).hexdigest(),
        )
        return RunStreamEvent(
            event_id=event_id,
            event_type=event_type,
            run_id=self._run_id,
            thread_id=self._thread_id,
            run_attempt_id=self._run_attempt_id,
            harness_run_id=source.run_id,
            item_id=_event_item_id(source.run_id, event_type, payload, event_id=event_id),
            occurred_at=source.occurred_at,
            payload=payload,
        )


def item_id_for_semantic_key(harness_run_id: str, kind: str, semantic_key: str) -> str:
    """Return the stable Item identity owned by one Harness Run projection."""

    return _stable_id("item", harness_run_id, kind, semantic_key)


def _event_item_id(
    harness_run_id: str,
    event_type: str,
    payload: dict[str, JsonValue],
    *,
    event_id: str,
) -> str | None:
    if event_type in _TEXT_EVENT_TYPES:
        key = payload.get("messageId")
        kind = "message"
    elif event_type in _REASONING_EVENT_TYPES:
        key = payload.get("messageId") or payload.get("entityId")
        kind = "reasoning"
    elif event_type in _TOOL_CALL_EVENT_TYPES:
        key = payload.get("toolCallId")
        kind = "tool_call"
    elif event_type == "TOOL_CALL_RESULT":
        key = payload.get("messageId")
        kind = "tool_result"
    elif event_type in _ACTIVITY_EVENT_TYPES:
        key = payload.get("messageId")
        kind = "activity"
    elif event_type == "RUN_FINISHED":
        key = "terminal-result"
        kind = "run_output"
    elif event_type == "RUN_ERROR":
        key = event_id
        kind = "error"
    else:
        return None
    if not isinstance(key, str) or not key:
        raise RunStreamProjectionError(f"{event_type} is missing its semantic Item identity")
    return item_id_for_semantic_key(harness_run_id, kind, key)


def _canonical_event_payload(event: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        rfc8785.dumps(event)
    except rfc8785.CanonicalizationError as error:
        raise RunStreamProjectionError("Agent Stream Protocol event is not canonical JSON") from error
    return event


def _without_terminal_result(event: dict[str, JsonValue]) -> dict[str, JsonValue]:
    bounded = dict(event)
    bounded["result"] = None
    raw = bounded.get("rawEvent")
    bounded["rawEvent"] = {**raw, "result_omitted": True} if isinstance(raw, dict) else {"result_omitted": True}
    return bounded


def _stable_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()
    return f"{kind}_{digest[:24]}"


def _stream_id(value: bytes | str | None) -> str:
    if value is None:
        raise RunStreamProjectionError("Redis returned an empty Run Stream cursor")
    return value.decode() if isinstance(value, bytes) else value


__all__ = [
    "DEFAULT_MAX_RUN_STREAM_ENTRIES",
    "DEFAULT_MAX_RUN_STREAM_EVENT_BYTES",
    "RUN_STREAM_FIELD",
    "RUN_STREAM_OPEN_EVENT_TYPE",
    "RUN_STREAM_OPEN_ID",
    "RunStreamProjectionError",
    "RunStreamProjector",
    "item_id_for_semantic_key",
]
