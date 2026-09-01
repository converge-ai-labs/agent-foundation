"""Detached continuation-backed Session application values."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_ui.composition import SnapshotReference

_SESSION_ID = Annotated[str, Field(pattern=r"^session-[0-9a-f]{16,64}$")]
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Base for immutable detached values crossing the application boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


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
    source_continuation_digest: _DIGEST
    source_agent_digest: _DIGEST
    source_environment_digest: _DIGEST


class ContinuationRef(StrictModel):
    object_digest: _DIGEST


class LocalSession(StrictModel):
    session_id: _SESSION_ID
    created_at: datetime
    updated_at: datetime
    title: str | None = Field(default=None, max_length=512)
    archived_at: datetime | None = None
    pinned: bool = False
    agent_snapshot: SnapshotReference
    environment_snapshot: SnapshotReference
    skill_selections: tuple[SessionAgentSkillSelection, ...] = ()
    parent_fork: SessionForkRef | None = None
    continuation: ContinuationRef

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
    title: str | None
    archived_at: datetime | None
    pinned: bool
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


class StoredSessionContinuation(StrictModel):
    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    harness_state: JsonValue
    deferred_requests: JsonValue | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SessionRunStatus(StrEnum):
    completed = "completed"
    suspended = "suspended"
    failed = "failed"
    cancelled = "cancelled"


class SessionRunResult(StrictModel):
    run_id: str = Field(min_length=1, max_length=128)
    status: SessionRunStatus
    output: JsonValue | None = None
    failure: JsonValue | None = None
    continuation: ContinuationRef | None = None


__all__ = [
    "ContinuationRef",
    "LocalSession",
    "SessionAgentSkillSelection",
    "SessionForkRef",
    "SessionRunResult",
    "SessionRunStatus",
    "SessionSummary",
    "SessionUpdate",
    "StoredSessionContinuation",
    "StrictModel",
]
