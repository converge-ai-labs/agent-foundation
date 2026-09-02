"""Typed detached contracts for Agent UI durable heads and immutable payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from a13n_environment_provider import EnvironmentProviderSafeError, EnvironmentState
from a13n_harness import HarnessState, SafeFailure
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic_ai.tools import DeferredToolRequests

from .objects import ObjectKind, ObjectRef

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type SchemaVersion = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
type ExecutionStatus = Literal["running", "succeeded", "failed", "cancelled", "lost"]


class StoredContract(BaseModel):
    """Immutable strict base for values crossing the persistence boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SnapshotRef(StoredContract):
    """Exact immutable snapshot selected by configuration or a Session."""

    snapshot_kind: Literal["agent", "environment"]
    object: ObjectRef

    @model_validator(mode="after")
    def _matching_object_kind(self) -> Self:
        expected = {
            "agent": ObjectKind.agent_snapshot,
            "environment": ObjectKind.environment_snapshot,
        }[self.snapshot_kind]
        if self.object.object_kind is not expected:
            raise ValueError("snapshot kind does not match immutable object kind")
        return self


class StoredSessionContinuation(StoredContract):
    """Complete root Harness continuation authority."""

    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    harness_state: HarnessState
    deferred_requests: DeferredToolRequests | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)


class CompactChildActivity(StoredContract):
    """One closed, bounded child activity retained for inspection."""

    kind: Literal["text", "thinking", "tool", "failure", "completion"]
    text: str | None = Field(default=None, max_length=32 * 1024)
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    arguments: JsonValue | None = None
    result: JsonValue | None = None

    @model_validator(mode="after")
    def _consistent_shape(self) -> Self:
        if self.kind == "tool":
            if self.tool_name is None:
                raise ValueError("tool activities require tool_name")
        elif self.tool_name is not None or self.arguments is not None or self.result is not None:
            raise ValueError("only tool activities may contain tool fields")
        if self.kind in {"text", "thinking", "failure"} and self.text is None:
            raise ValueError(f"{self.kind} activities require text")
        return self


class CompactChildDisplay(StoredContract):
    """Bounded closed AG-UI projection for one child execution segment."""

    activities: tuple[CompactChildActivity, ...] = Field(default=(), max_length=512)
    final_answer: str | None = Field(default=None, max_length=64 * 1024)


class StoredChildCheckpoint(StoredContract):
    """Exact child Harness continuation plus detached compact display."""

    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=80)
    child_thread_id: str = Field(min_length=1, max_length=80)
    child_run_id: str = Field(min_length=1, max_length=80)
    segment_index: int = Field(ge=0)
    harness_state: HarnessState
    display: CompactChildDisplay
    terminal: bool
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _matching_thread(self) -> Self:
        if self.harness_state.thread_id != self.child_thread_id:
            raise ValueError("checkpoint HarnessState must belong to child_thread_id")
        return self


class EnvironmentBindingKey(StoredContract):
    """Complete private key for one Host-authoritative bound-folder state."""

    session_id: str = Field(min_length=1, max_length=80)
    profile_digest: Digest
    binder_key: str = Field(min_length=3, max_length=160)
    normalized_folder: str = Field(min_length=1, max_length=4096)


class StoredEnvironmentState(StoredContract):
    """Validated immutable Environment state tied to its exact binding provenance."""

    schema_version: Literal["1"] = "1"
    binding: EnvironmentBindingKey
    provider_schema_version: SchemaVersion
    state: EnvironmentState
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)


class Session(StoredContract):
    """Detached Session metadata and selected root continuation head."""

    session_id: str
    root_thread_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None
    archived_at: datetime | None
    pinned: bool
    status: Literal["active", "deleting"]
    agent_snapshot: SnapshotRef
    environment_snapshot: SnapshotRef
    parent_fork: JsonValue | None
    continuation: ObjectRef


class ChildExecutionHead(StoredContract):
    """Detached mutable head for one child execution segment."""

    execution_id: str
    session_id: str
    parent_thread_id: str
    child_thread_id: str
    child_run_id: str
    segment_index: int
    subagent_name: str
    child_definition_id: str
    child_definition_digest: Digest
    input: str
    status: ExecutionStatus
    selected_checkpoint: ObjectRef | None
    selected_checkpoint_terminal: bool
    resumable: bool
    resumed_from: str | None
    failure: SafeFailure | None
    owner_process_generation: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class EnvironmentBindingHead(StoredContract):
    """Detached current Environment-state selection and cleanup facts."""

    key: EnvironmentBindingKey
    state: ObjectRef | None
    cleanup_status: Literal["none", "required", "in_progress", "failed"]
    cleanup_failure: EnvironmentProviderSafeError | None
    updated_at: datetime


__all__ = [
    "ChildExecutionHead",
    "CompactChildActivity",
    "CompactChildDisplay",
    "EnvironmentBindingHead",
    "EnvironmentBindingKey",
    "ExecutionStatus",
    "SafeFailure",
    "Session",
    "SnapshotRef",
    "StoredChildCheckpoint",
    "StoredEnvironmentState",
    "StoredSessionContinuation",
]
