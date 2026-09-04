"""Detached bounded values exposed by the Agent UI application boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_ui.storage import AgentResourceSource, MarkdownSubagentSource

_MAX_FAILURE_MESSAGE = 32 * 1024
_MAX_DEFERRED_RESPONSE_BYTES = 1024 * 1024


class SurfaceModel(BaseModel):
    """Strict immutable base for values crossing an Agent UI surface boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AgentSourceView(SurfaceModel):
    kind: Literal["agent", "markdown"]
    id: str = Field(min_length=1, max_length=128)

    @classmethod
    def from_stored(cls, source: AgentResourceSource | MarkdownSubagentSource) -> Self:
        return cls(kind=source.kind, id=source.id)


class ThreadConfigurationView(SurfaceModel):
    version: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=128)
    agent_source: AgentSourceView
    environment_profile_id: str = Field(min_length=1, max_length=128)
    harness_plugin_ids: tuple[str, ...] = ()
    environment_run_extension_ids: tuple[str, ...] = ()
    mcp_server_ids: tuple[str, ...] = ()


class RootActivityState(StrEnum):
    inactive = "inactive"
    preparing = "preparing"
    running = "running"


class RootActivityView(SurfaceModel):
    state: RootActivityState
    receipt_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=80)
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()

    @model_validator(mode="after")
    def _consistent_state(self) -> Self:
        if self.state is RootActivityState.inactive:
            if self.receipt_id is not None or self.run_id is not None or self.available_actions:
                raise ValueError("inactive root activity cannot expose operation authority")
        elif self.receipt_id is None:
            raise ValueError("active root activity requires a receipt")
        if self.state is RootActivityState.preparing and self.run_id is not None:
            raise ValueError("preparing root activity cannot have a Run ID")
        return self


class ThreadSummary(SurfaceModel):
    thread_id: str = Field(min_length=1, max_length=80)
    parent_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime
    metadata_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=512)
    archived: bool
    configuration: ThreadConfigurationView
    continuation_state: Literal["initial", "selected"]
    root_activity: RootActivityView

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Thread timestamps must include a UTC offset")
        return value.astimezone(UTC)


class ThreadPage(SurfaceModel):
    threads: tuple[ThreadSummary, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class ThreadMetadataPatch(SurfaceModel):
    """Patch whose fields-set distinguishes an omitted title from a cleared title."""

    title: str | None = Field(default=None, max_length=512)
    archived: bool | None = None

    @model_validator(mode="after")
    def _valid_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Thread metadata patch must not be empty")
        if "archived" in self.model_fields_set and self.archived is None:
            raise ValueError("archived cannot be null when supplied")
        return self


class ThreadMetadataMutation(SurfaceModel):
    expected_version: int = Field(ge=1)
    patch: ThreadMetadataPatch


class DeferredRequestView(SurfaceModel):
    request_id: str = Field(min_length=1, max_length=256)
    kind: Literal["approval", "external"]
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: JsonValue | None = None
    arguments_omitted: bool = False
    metadata: dict[str, JsonValue] | None = None
    metadata_omitted: bool = False


class ThreadDetail(SurfaceModel):
    thread: ThreadSummary
    continuation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    deferred_requests: tuple[DeferredRequestView, ...] = ()
    available_actions: tuple[Literal["run", "respond", "wait", "steer", "cancel", "archive"], ...] = ()


class TranscriptPart(SurfaceModel):
    kind: Literal[
        "system",
        "user",
        "assistant",
        "thinking",
        "tool_call",
        "tool_result",
        "retry",
        "media",
        "other",
    ]
    text: str | None = Field(default=None, max_length=64 * 1024)
    tool_name: str | None = Field(default=None, max_length=128)
    tool_call_id: str | None = Field(default=None, max_length=256)
    value: JsonValue | None = None
    value_omitted: bool = False


class TranscriptEntry(SurfaceModel):
    position: int = Field(ge=0)
    message_kind: Literal["request", "response"]
    timestamp: datetime | None = None
    parts: tuple[TranscriptPart, ...]

    @field_validator("timestamp")
    @classmethod
    def _optional_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Transcript timestamps must include a UTC offset")
        return value.astimezone(UTC)


class TranscriptPage(SurfaceModel):
    entries: tuple[TranscriptEntry, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class EnvironmentProfileSummary(SurfaceModel):
    profile_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    mode: Literal["full-control", "sandbox", "custom"]
    description: str = Field(min_length=1, max_length=1024)
    provider_key: str = Field(min_length=1, max_length=200)
    release_owned: bool
    canonical_host_paths: bool


class ProjectSummary(SurfaceModel):
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    position: int
    roots: tuple[str, ...] = Field(min_length=1, max_length=64)
    last_active_at: datetime | None = None

    @field_validator("last_active_at")
    @classmethod
    def _optional_project_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Project recency must include a UTC offset")
        return value.astimezone(UTC)


class FailureView(SurfaceModel):
    code: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=_MAX_FAILURE_MESSAGE)
    details: dict[str, JsonValue] | None = None
    retry_hint: str | None = Field(default=None, max_length=128)


class ContinuationSelectionView(SurfaceModel):
    status: Literal["selected", "not_available", "failed"]
    continuation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure: FailureView | None = None


class EnvironmentOutcomeView(SurfaceModel):
    unchanged: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)
    cleanup_failures: tuple[FailureView, ...] = ()


class RootExecutionView(SurfaceModel):
    status: Literal["completed", "failed", "cancelled", "suspended"]
    output: JsonValue | None = None
    output_omitted: bool = False
    failure: FailureView | None = None
    usage: dict[str, JsonValue] | None = None


class RootRunOutcomeView(SurfaceModel):
    execution: RootExecutionView
    continuation: ContinuationSelectionView
    environment: EnvironmentOutcomeView
    composition_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class RootOperationStatus(StrEnum):
    preparing = "preparing"
    running = "running"
    completed = "completed"
    suspended = "suspended"
    failed = "failed"
    cancelled = "cancelled"


class RootRunReceipt(SurfaceModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=80)
    submitted_at: datetime

    @field_validator("submitted_at")
    @classmethod
    def _receipt_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Receipt timestamp must include a UTC offset")
        return value.astimezone(UTC)


class RootOperationView(SurfaceModel):
    receipt: RootRunReceipt
    status: RootOperationStatus
    run_id: str | None = Field(default=None, min_length=1, max_length=80)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: RootRunOutcomeView | None = None
    failure: FailureView | None = None
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()

    @field_validator("started_at", "completed_at")
    @classmethod
    def _operation_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Operation timestamps must include a UTC offset")
        return value.astimezone(UTC)


class RootControlResult(SurfaceModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    enqueue_id: str | None = Field(default=None, min_length=1, max_length=256)


class ApprovalDecision(SurfaceModel):
    kind: Literal["approval"] = "approval"
    request_id: str = Field(min_length=1, max_length=256)
    approved: bool
    override_arguments: dict[str, JsonValue] | None = None
    denial_message: str | None = Field(default=None, max_length=32 * 1024)

    @model_validator(mode="after")
    def _consistent_decision(self) -> Self:
        if self.approved and self.denial_message is not None:
            raise ValueError("approved decisions cannot include a denial message")
        if not self.approved and self.override_arguments is not None:
            raise ValueError("denied decisions cannot override arguments")
        return self


class ExternalToolResult(SurfaceModel):
    kind: Literal["external"] = "external"
    request_id: str = Field(min_length=1, max_length=256)
    result: JsonValue | None = None
    denied: bool = False
    denial_message: str | None = Field(default=None, max_length=32 * 1024)

    @model_validator(mode="after")
    def _consistent_result(self) -> Self:
        if self.denied and self.denial_message is None:
            raise ValueError("denied external results require a message")
        if self.denied and self.result is not None:
            raise ValueError("denied external results cannot include a result")
        if not self.denied and self.denial_message is not None:
            raise ValueError("successful external results cannot include a denial message")
        return self


type DeferredResponseItem = Annotated[ApprovalDecision | ExternalToolResult, Field(discriminator="kind")]


class ThreadDeferredResponse(SurfaceModel):
    expected_continuation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses: tuple[DeferredResponseItem, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _unique_requests(self) -> Self:
        request_ids = tuple(item.request_id for item in self.responses)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Deferred response request IDs must be unique")
        if len(self.model_dump_json().encode("utf-8")) > _MAX_DEFERRED_RESPONSE_BYTES:
            raise ValueError("Deferred response exceeds the surface payload limit")
        return self


class ChildToolCallView(SurfaceModel):
    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    status: Literal["running", "success", "failed", "denied", "interrupted"]
    arguments: JsonValue | None = None
    result: JsonValue | None = None


class ChildActivityView(SurfaceModel):
    sequence: int = Field(ge=0)
    output_preview: str = Field(default="", max_length=32 * 1024)
    output_truncated: bool = False
    active_tool_calls: tuple[ChildToolCallView, ...] = Field(default=(), max_length=20)
    recent_tool_calls: tuple[ChildToolCallView, ...] = Field(default=(), max_length=20)
    dropped_tool_calls: int = Field(default=0, ge=0)


class ChildExecutionView(SurfaceModel):
    execution_id: str = Field(min_length=1, max_length=80)
    root_thread_id: str = Field(min_length=1, max_length=80)
    parent_thread_id: str = Field(min_length=1, max_length=80)
    child_thread_id: str = Field(min_length=1, max_length=80)
    child_run_id: str = Field(min_length=1, max_length=80)
    segment_index: int = Field(ge=0)
    composition_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    subagent_name: str = Field(min_length=1, max_length=128)
    child_definition_id: str = Field(min_length=1, max_length=256)
    persisted_status: Literal["running", "succeeded", "failed", "cancelled", "lost"]
    local_status: Literal["active", "unavailable"]
    resumed_from: str | None = Field(default=None, min_length=1, max_length=80)
    failure: FailureView | None = None
    resumable: bool
    activity: ChildActivityView
    available_actions: tuple[Literal["wait", "steer", "cancel"], ...] = ()
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @field_validator("created_at", "updated_at", "completed_at")
    @classmethod
    def _child_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Child execution timestamps must include a UTC offset")
        return value.astimezone(UTC)


class ChildExecutionPage(SurfaceModel):
    executions: tuple[ChildExecutionView, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class ChildControlResult(SurfaceModel):
    execution_id: str = Field(min_length=1, max_length=80)
    accepted: bool
    enqueue_id: str | None = Field(default=None, min_length=1, max_length=256)
    persisted_status: Literal["running", "succeeded", "failed", "cancelled", "lost"] | None = None


class ThreadFocusSnapshot(SurfaceModel):
    epoch: str = Field(min_length=1, max_length=80)
    cutover_sequence: int = Field(ge=0)
    thread: ThreadDetail
    children: ChildExecutionPage


__all__ = [
    "AgentSourceView",
    "ApprovalDecision",
    "ChildActivityView",
    "ChildControlResult",
    "ChildExecutionPage",
    "ChildExecutionView",
    "ChildToolCallView",
    "ContinuationSelectionView",
    "DeferredRequestView",
    "DeferredResponseItem",
    "EnvironmentOutcomeView",
    "EnvironmentProfileSummary",
    "ExternalToolResult",
    "FailureView",
    "ProjectSummary",
    "RootActivityState",
    "RootActivityView",
    "RootControlResult",
    "RootExecutionView",
    "RootOperationStatus",
    "RootOperationView",
    "RootRunOutcomeView",
    "RootRunReceipt",
    "SurfaceModel",
    "ThreadConfigurationView",
    "ThreadDeferredResponse",
    "ThreadDetail",
    "ThreadFocusSnapshot",
    "ThreadMetadataMutation",
    "ThreadMetadataPatch",
    "ThreadPage",
    "ThreadSummary",
    "TranscriptEntry",
    "TranscriptPage",
    "TranscriptPart",
]
