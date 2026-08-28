"""Detached Session, Thread, Turn, and checkpoint application values."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_ui.composition import SnapshotReference

_SESSION_ID = Annotated[str, Field(pattern=r"^session-[0-9a-f]{16,64}$")]
_THREAD_ID = Annotated[str, Field(min_length=1, max_length=128)]
_TURN_ID = Annotated[str, Field(pattern=r"^turn-[0-9a-f]{16,64}$")]
_CHECKPOINT_ID = Annotated[str, Field(pattern=r"^checkpoint-[0-9a-f]{16,64}$")]
_REQUEST_ID = Annotated[str, Field(min_length=1, max_length=128)]
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Base for immutable detached values crossing the application boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SessionLifecycleState(StrEnum):
    provisioning = "provisioning"
    ready = "ready"
    blocked = "blocked"
    deleting = "deleting"
    cleanup_pending = "cleanup_pending"
    deleted = "deleted"


class TurnState(StrEnum):
    accepted = "accepted"
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


class WaitingReason(StrEnum):
    deferred_tool = "deferred_tool"
    approval = "approval"
    external_input = "external_input"


class SessionAgentSkillSelection(StrictModel):
    agent_node_id: str = Field(min_length=3, max_length=128)
    mode: Literal["agent_default", "exact"]
    names: tuple[str, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def _consistent_selection(self) -> Self:
        if self.mode == "agent_default" and self.names:
            raise ValueError("agent_default Skill selection cannot include names")
        if len(self.names) != len(set(self.names)):
            raise ValueError("Skill selection names must be unique")
        return self


class SessionForkRef(StrictModel):
    source_session_id: _SESSION_ID
    source_thread_id: _THREAD_ID
    source_thread_commit_revision: int = Field(ge=0)
    source_checkpoint_id: _CHECKPOINT_ID | None = None
    source_agent_digest: _DIGEST
    source_environment_digest: _DIGEST


class CheckpointRef(StrictModel):
    checkpoint_id: _CHECKPOINT_ID
    thread_id: _THREAD_ID
    state_object_digest: _DIGEST
    harness_release: str = Field(min_length=1, max_length=128)


class PendingDeferredRef(StrictModel):
    object_digest: _DIGEST
    request_digest: _DIGEST
    request_codec_version: str = Field(min_length=1, max_length=64)
    source_run_id: str = Field(min_length=1, max_length=128)
    consumed_by_run_id: str | None = Field(default=None, min_length=1, max_length=128)


class TurnView(StrictModel):
    turn_id: _TURN_ID
    session_id: _SESSION_ID
    thread_id: _THREAD_ID
    input: JsonValue
    state: TurnState
    waiting_reason: WaitingReason | None = None
    pending_deferred: PendingDeferredRef | None = None
    base_checkpoint: CheckpointRef | None = None
    run_ids: tuple[str, ...] = ()
    terminal_projection: JsonValue | None = None
    failure: JsonValue | None = None
    selected_checkpoint: CheckpointRef | None = None
    accepted_at: datetime
    finished_at: datetime | None = None

    @field_validator("accepted_at", "finished_at")
    @classmethod
    def _normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _lifecycle_consistency(self) -> Self:
        terminal = self.state in {
            TurnState.completed,
            TurnState.failed,
            TurnState.cancelled,
            TurnState.interrupted,
        }
        if terminal != (self.finished_at is not None):
            raise ValueError("terminal Turn state and finished_at must agree")
        if self.state is TurnState.waiting:
            if self.waiting_reason is None or self.pending_deferred is None:
                raise ValueError("waiting Turn requires reason and pending deferred input")
        elif self.waiting_reason is not None or self.pending_deferred is not None:
            raise ValueError("only a waiting Turn can expose pending deferred input")
        return self


class ThreadView(StrictModel):
    thread_id: _THREAD_ID
    session_id: _SESSION_ID
    commit_revision: int = Field(ge=0)
    queue_revision: int = Field(ge=0)
    selected_checkpoint: CheckpointRef | None = None
    active_turn_id: _TURN_ID | None = None
    turns: tuple[TurnView, ...] = ()


class LocalSession(StrictModel):
    session_id: _SESSION_ID
    creation_request_id: _REQUEST_ID
    lifecycle_state: SessionLifecycleState
    lifecycle_failure: JsonValue | None = None
    created_at: datetime
    updated_at: datetime
    title: str | None = Field(default=None, max_length=512)
    archived_at: datetime | None = None
    pinned: bool = False
    display_order: int = 0
    control_revision: int = Field(ge=1)
    agent_snapshot: SnapshotReference
    environment_snapshot: SnapshotReference
    skill_selections: tuple[SessionAgentSkillSelection, ...] = ()
    parent_fork: SessionForkRef | None = None
    root: ThreadView

    @field_validator("created_at", "updated_at", "archived_at")
    @classmethod
    def _normalize_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SessionSummary(StrictModel):
    session_id: _SESSION_ID
    lifecycle_state: SessionLifecycleState
    title: str | None
    archived_at: datetime | None
    pinned: bool
    display_order: int
    control_revision: int = Field(ge=1)
    thread_id: _THREAD_ID
    thread_commit_revision: int = Field(ge=0)
    active_turn_id: _TURN_ID | None
    updated_at: datetime

    @field_validator("archived_at", "updated_at")
    @classmethod
    def _normalize_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SessionUpdate(StrictModel):
    title: str | None = Field(default=None, max_length=512)
    archived: bool | None = None
    pinned: bool | None = None
    display_order: int | None = None


class StoredHarnessState(StrictModel):
    session_id: _SESSION_ID
    thread_id: _THREAD_ID
    owner_kind: Literal["session_baseline", "session_fork", "root_turn"]
    turn_id: _TURN_ID | None = None
    checkpoint_id: _CHECKPOINT_ID
    harness_release: str = Field(min_length=1, max_length=128)
    harness_state_schema: str = Field(min_length=1, max_length=64)
    harness_state: JsonValue
    exported_at: datetime

    @field_validator("exported_at")
    @classmethod
    def _normalize_exported_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _owner_consistency(self) -> Self:
        if (self.owner_kind == "root_turn") != (self.turn_id is not None):
            raise ValueError("root Turn state requires exactly one turn_id")
        return self


class StoredDeferredRequests(StrictModel):
    session_id: _SESSION_ID
    thread_id: _THREAD_ID
    owner_kind: Literal["root_turn"] = "root_turn"
    turn_id: _TURN_ID
    source_run_id: str = Field(min_length=1, max_length=128)
    agent_snapshot_digest: _DIGEST
    harness_release: str = Field(min_length=1, max_length=128)
    request_codec_version: str = Field(min_length=1, max_length=64)
    request_digest: _DIGEST
    tool_surface_digest: _DIGEST
    requests: JsonValue
    exported_at: datetime

    @field_validator("exported_at")
    @classmethod
    def _normalize_exported_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = [
    "CheckpointRef",
    "LocalSession",
    "PendingDeferredRef",
    "SessionAgentSkillSelection",
    "SessionForkRef",
    "SessionLifecycleState",
    "SessionSummary",
    "SessionUpdate",
    "StoredDeferredRequests",
    "StoredHarnessState",
    "StrictModel",
    "ThreadView",
    "TurnState",
    "TurnView",
    "WaitingReason",
]
