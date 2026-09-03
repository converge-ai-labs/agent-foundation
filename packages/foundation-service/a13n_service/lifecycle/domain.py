"""Durable lifecycle fact and projection state contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

import rfc8785
from a13n_harness import SafeFailure
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from a13n_service.ids import new_object_id

MAX_LIFECYCLE_PAYLOAD_BYTES = 64 * 1024

LifecycleEventId = Annotated[str, StringConstraints(pattern=r"^lev_[a-z0-9]{16,64}$")]
MutationId = Annotated[str, StringConstraints(pattern=r"^mut_[a-z0-9]{16,64}$")]
ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=72)]
SchemaVersion = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,31}$")]
JsonObject = dict[str, JsonValue]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_utc)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LifecycleEntityType(StrEnum):
    run = "run"
    run_attempt = "run_attempt"


class LifecycleProjectionState(StrEnum):
    pending = "pending"
    projecting = "projecting"
    retry_wait = "retry_wait"
    projected = "projected"
    abandoned = "abandoned"


RUN_EVENT_TYPES = frozenset(
    {
        "run.accepted",
        "run.running",
        "run.waiting",
        "run.completed",
        "run.failed",
        "run.cancelled",
    }
)
RUN_ATTEMPT_EVENT_TYPES = frozenset(
    {
        "run_attempt.leased",
        "run_attempt.running",
        "run_attempt.succeeded",
        "run_attempt.yielded",
        "run_attempt.failed",
        "run_attempt.cancelled",
    }
)
LIFECYCLE_EVENT_TYPES = RUN_EVENT_TYPES | RUN_ATTEMPT_EVENT_TYPES


class LifecycleEventDraft(_StrictModel):
    """One fact prepared by the transaction that owns the state mutation."""

    id: LifecycleEventId = Field(default_factory=lambda: new_object_id("lev"))
    tenant_id: ResourceId
    entity_type: LifecycleEntityType
    entity_id: ResourceId
    entity_version: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=128)
    schema_version: SchemaVersion = "1"
    mutation_id: MutationId
    session_id: ResourceId | None = None
    thread_id: ResourceId | None = None
    run_id: ResourceId
    run_attempt_id: ResourceId | None = None
    payload: JsonObject
    actor_type: str = Field(min_length=1, max_length=32)
    actor_id: ResourceId | None = None
    occurred_at: UtcDateTime
    project_live: bool = True

    @model_validator(mode="after")
    def validate_source(self) -> LifecycleEventDraft:
        registry = RUN_EVENT_TYPES if self.entity_type is LifecycleEntityType.run else RUN_ATTEMPT_EVENT_TYPES
        if self.event_type not in registry:
            raise ValueError("event type does not belong to the lifecycle entity")
        if self.entity_type is LifecycleEntityType.run:
            if self.entity_id != self.run_id or self.run_attempt_id is not None:
                raise ValueError("Run lifecycle facts require the Run as their sole entity")
        elif self.run_attempt_id is None or self.entity_id != self.run_attempt_id:
            raise ValueError("RunAttempt lifecycle facts require matching Run and RunAttempt correlation")
        if len(rfc8785.dumps(self.payload)) > MAX_LIFECYCLE_PAYLOAD_BYTES:
            raise ValueError("lifecycle payload exceeds the encoded size limit")
        return self


class LifecycleEvent(_StrictModel):
    seq: int = Field(ge=1)
    id: LifecycleEventId
    tenant_id: ResourceId
    entity_type: LifecycleEntityType
    entity_id: ResourceId
    resource_seq: int = Field(ge=1)
    entity_version: int = Field(ge=1)
    event_type: str
    schema_version: SchemaVersion
    mutation_id: MutationId
    session_id: ResourceId | None = None
    thread_id: ResourceId | None = None
    run_id: ResourceId
    run_attempt_id: ResourceId | None = None
    payload: JsonObject
    actor_type: str
    actor_id: ResourceId | None = None
    occurred_at: UtcDateTime
    created_at: UtcDateTime
    projection_state: LifecycleProjectionState
    projection_attempts: int = Field(ge=0)
    projection_next_attempt_at: UtcDateTime | None = None
    projection_lease_owner: str | None = None
    projection_lease_expires_at: UtcDateTime | None = None
    projected_at: UtcDateTime | None = None
    projection_error: SafeFailure | None = None


def new_mutation_id() -> str:
    return new_object_id("mut")


def deterministic_mutation_id(*parts: str) -> str:
    """Build retry-stable mutation identity from non-secret canonical facts."""

    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return f"mut_{digest}"


__all__ = [
    "LIFECYCLE_EVENT_TYPES",
    "MAX_LIFECYCLE_PAYLOAD_BYTES",
    "RUN_ATTEMPT_EVENT_TYPES",
    "RUN_EVENT_TYPES",
    "LifecycleEntityType",
    "LifecycleEvent",
    "LifecycleEventDraft",
    "LifecycleProjectionState",
    "deterministic_mutation_id",
    "new_mutation_id",
]
