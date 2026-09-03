"""Typed Run Stream, retained Item, and replay snapshot values."""

from __future__ import annotations

import hashlib
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from a13n_service.interactions.domain import (
    BoundedKey,
    BoundedName,
    JsonObject,
    ObjectId,
    RunPayloadObjectRef,
    Sha256Digest,
    StrictModel,
    ThreadId,
    UtcDateTime,
)

RedisStreamId = Annotated[str, StringConstraints(pattern=r"^[0-9]+-[0-9]+$", max_length=64)]


class RunStreamEvent(StrictModel):
    """One bounded presentation observation carried by a Run-scoped Redis Stream."""

    schema_version: Literal["1"] = "1"
    event_id: ObjectId
    event_type: BoundedKey
    run_id: ObjectId
    thread_id: ThreadId
    run_attempt_id: ObjectId | None = None
    harness_run_id: BoundedName | None = None
    lifecycle_event_id: ObjectId | None = None
    item_id: ObjectId | None = None
    occurred_at: UtcDateTime
    payload: JsonObject


class RetainedRunStreamEvent(StrictModel):
    """One exact Redis entry retained in an immutable replay snapshot."""

    stream_id: RedisStreamId
    event: RunStreamEvent


class RetainedItem(StrictModel):
    """One user-visible semantic unit reconstructed from complete Run Stream events."""

    id: ObjectId
    kind: BoundedKey
    state: Literal["completed", "interrupted", "failed"]
    parent_item_id: ObjectId | None = None
    first_stream_id: RedisStreamId
    last_stream_id: RedisStreamId
    content: JsonValue

    @model_validator(mode="after")
    def stream_range_is_ordered(self) -> RetainedItem:
        if _stream_position(self.last_stream_id) < _stream_position(self.first_stream_id):
            raise ValueError("retained Item stream range is reversed")
        if self.parent_item_id == self.id:
            raise ValueError("retained Item cannot parent itself")
        return self


class RunOutputItemContent(StrictModel):
    """Selected completed Run output retained as one terminal Item."""

    schema_version: Literal["1"] = "1"
    result_digest: Sha256Digest
    output: JsonValue | None = None
    output_object: RunPayloadObjectRef | None = None

    @model_validator(mode="after")
    def output_representation_is_exclusive(self) -> RunOutputItemContent:
        inline = "output" in self.model_fields_set
        if inline == (self.output_object is not None):
            raise ValueError("Run output Item requires exactly one inline or object representation")
        return self

    def as_json(self) -> JsonObject:
        value = self.model_dump(mode="json", by_alias=True)
        if "output" not in self.model_fields_set:
            value.pop("output", None)
        if self.output_object is None:
            value.pop("output_object", None)
        return value


class RunReplaySnapshot(StrictModel):
    """Complete immutable retained presentation for one sealed Run."""

    schema_version: Literal["1"] = "1"
    run_id: ObjectId
    thread_id: ThreadId
    stream_key_digest_sha256: Sha256Digest
    first_stream_id: RedisStreamId
    last_stream_id: RedisStreamId
    closed_at: UtcDateTime
    source_run_attempt_ids: tuple[ObjectId, ...] = Field(min_length=1)
    events: tuple[RetainedRunStreamEvent, ...] = Field(min_length=1)
    items: tuple[RetainedItem, ...]

    @field_validator("source_run_attempt_ids")
    @classmethod
    def source_attempts_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("replay source RunAttempt IDs must be unique")
        return value

    @model_validator(mode="after")
    def replay_is_complete_and_coherent(self) -> RunReplaySnapshot:
        positions = tuple(_stream_position(retained.stream_id) for retained in self.events)
        if any(current <= previous for previous, current in pairwise(positions)):
            raise ValueError("replay stream IDs must be strictly increasing")
        if self.first_stream_id != self.events[0].stream_id or self.last_stream_id != self.events[-1].stream_id:
            raise ValueError("replay stream boundary does not match retained events")
        if any(event.event.run_id != self.run_id or event.event.thread_id != self.thread_id for event in self.events):
            raise ValueError("replay event identity does not match its Run and Thread")
        if self.closed_at < max(event.event.occurred_at for event in self.events):
            raise ValueError("replay cannot close before its retained events")
        observed_attempts = tuple(
            dict.fromkeys(event.event.run_attempt_id for event in self.events if event.event.run_attempt_id is not None)
        )
        if observed_attempts != self.source_run_attempt_ids:
            raise ValueError("replay source RunAttempts do not match retained event provenance")
        events_by_id: dict[str, RunStreamEvent] = {}
        for retained in self.events:
            previous = events_by_id.setdefault(retained.event.event_id, retained.event)
            if previous != retained.event:
                raise ValueError("replay contains conflicting events under one stable identity")
        item_by_id = {item.id: item for item in self.items}
        if len(item_by_id) != len(self.items):
            raise ValueError("replay Item IDs must be unique")
        observed_item_ids = {event.event.item_id for event in self.events if event.event.item_id is not None}
        if observed_item_ids != set(item_by_id):
            raise ValueError("replay Items do not exactly cover Item-bearing events")
        for item in self.items:
            if item.parent_item_id is not None and item.parent_item_id not in item_by_id:
                raise ValueError("retained Item parent is absent from the replay")
            item_stream_ids = tuple(event.stream_id for event in self.events if event.event.item_id == item.id)
            if item_stream_ids[0] != item.first_stream_id or item_stream_ids[-1] != item.last_stream_id:
                raise ValueError("retained Item range does not match its events")
        return self


def run_stream_key(tenant_id: str, run_id: str) -> str:
    """Return the internal tenant-scoped Redis key for one stable Run Stream."""

    return f"a13n:tenants:{tenant_id}:runs:{run_id}:stream:v1"


def run_stream_key_digest(tenant_id: str, run_id: str) -> str:
    return hashlib.sha256(run_stream_key(tenant_id, run_id).encode()).hexdigest()


def run_replay_key(tenant_id: str, run_id: str) -> str:
    return f"tenants/{tenant_id}/runs/{run_id}/replay/version-1.json"


def _stream_position(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", 1)
    return int(milliseconds), int(sequence)


__all__ = [
    "RedisStreamId",
    "RetainedItem",
    "RetainedRunStreamEvent",
    "RunOutputItemContent",
    "RunReplaySnapshot",
    "RunStreamEvent",
    "run_replay_key",
    "run_stream_key",
    "run_stream_key_digest",
]
