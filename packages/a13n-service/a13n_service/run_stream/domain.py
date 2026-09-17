"""Run-scoped presentation event and retained replay contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from a13n_service.temporal import UtcDateTime

MAX_RUN_STREAM_PAYLOAD_BYTES = 256 * 1024

RunStreamEventId = Annotated[str, StringConstraints(pattern=r"^rse_[a-z0-9]{16,64}$")]
ItemId = Annotated[str, StringConstraints(pattern=r"^itm_[a-z0-9]{16,64}$")]
ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=72)]
RedisStreamId = Annotated[str, StringConstraints(pattern=r"^[0-9]+-[0-9]+$")]
EventType = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$", max_length=128)]
JsonObject = dict[str, JsonValue]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStreamEvent(_StrictModel):
    schema_version: Literal["1"] = "1"
    event_id: RunStreamEventId
    event_type: EventType
    run_id: ResourceId
    thread_id: ResourceId
    run_attempt_id: ResourceId | None = None
    harness_run_id: ResourceId | None = None
    lifecycle_event_id: ResourceId | None = None
    item_id: ItemId | None = None
    occurred_at: UtcDateTime
    payload: JsonObject

    @model_validator(mode="after")
    def validate_payload_bound(self) -> RunStreamEvent:
        if len(rfc8785.dumps(self.payload)) > MAX_RUN_STREAM_PAYLOAD_BYTES:
            raise ValueError("Run Stream payload exceeds the encoded size limit")
        if self.event_type == "run.recovery":
            RecoveryPayload.model_validate(self.payload)
            if self.run_attempt_id is None or self.lifecycle_event_id is None:
                raise ValueError("Recovery requires committed Attempt correlation")
        return self


class RetainedItem(_StrictModel):
    id: ItemId
    kind: str = Field(min_length=1, max_length=64)
    state: Literal["in_progress", "completed", "interrupted", "failed"]
    parent_item_id: ItemId | None = None
    first_stream_id: RedisStreamId
    last_stream_id: RedisStreamId
    content: JsonValue


@dataclass(frozen=True, slots=True)
class RunStreamEntry:
    stream_id: str
    event: RunStreamEvent


@dataclass(frozen=True, slots=True)
class RunStreamPage:
    items: tuple[RunStreamEntry, ...]
    next_stream_id: str | None
    retained_floor: str | None
    high_watermark: str | None
    closed: bool
    trimmed: bool
    closed_at: datetime | None = None
    pending_events: int = 0
    pending_bytes: int = 0


@dataclass(frozen=True, slots=True)
class CompleteRunStream:
    entries: tuple[RunStreamEntry, ...]
    closed_at: datetime
    stream_key_digest_sha256: str


class RunStreamError(RuntimeError):
    """Run presentation persistence is invalid or unavailable."""


class RunStreamClosed(RunStreamError):
    """An append targeted a stream that already reached its terminal boundary."""


class PublicationRejected(RunStreamError):
    """The Attempt no longer owns the Run's activated publication generation."""


class PublicationUnavailable(RunStreamError):
    """Publication activation or Redis continuity cannot be established safely."""


class PublicationContinuityLost(PublicationUnavailable):
    """This Run's publication history cannot be recovered by retrying a write."""


class PublicationPending(PublicationUnavailable):
    """A different unfinished publication must be resolved before this operation."""


class PublicationBackpressure(PublicationUnavailable):
    """Unpersisted presentation reached the configured admission bound."""


RecoveryReason = Literal["lease_expired", "retry_after_failure", "planned_handoff", "pending_input"]


class RecoveryPayload(_StrictModel):
    reason: RecoveryReason


@dataclass(frozen=True, slots=True)
class ActivationResult:
    leased_stream_id: str
    recovery_stream_id: str | None
    active: bool


class RunStreamReplayGap(RunStreamError):
    def __init__(self, *, retained_floor: str | None, high_watermark: str | None) -> None:
        super().__init__("requested Run Stream history is no longer retained")
        self.retained_floor = retained_floor
        self.high_watermark = high_watermark


class RetainedReplayUnavailable(RunStreamError):
    """A complete retained snapshot cannot be created from the live source."""


def deterministic_run_stream_event_id(*parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return f"rse_{digest}"


def deterministic_item_id(run_id: str, kind: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{kind}\0{source_id}".encode()).hexdigest()
    return f"itm_{digest}"


__all__ = [
    "MAX_RUN_STREAM_PAYLOAD_BYTES",
    "CompleteRunStream",
    "RetainedItem",
    "RetainedReplayUnavailable",
    "RunStreamClosed",
    "RunStreamEntry",
    "RunStreamError",
    "RunStreamEvent",
    "RunStreamPage",
    "RunStreamReplayGap",
    "deterministic_item_id",
    "deterministic_run_stream_event_id",
]
