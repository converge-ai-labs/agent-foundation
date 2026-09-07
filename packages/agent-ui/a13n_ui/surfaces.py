"""Detached bounded values exposed by the Agent UI application boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from a13n_stream_protocol import ContentMetadata
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_ui.live import LiveEvent
from a13n_ui.storage import AgentResourceSource, MarkdownSubagentSource

_MAX_FAILURE_MESSAGE = 32 * 1024
_MAX_DEFERRED_RESPONSE_BYTES = 1024 * 1024


class SurfaceModel(BaseModel):
    """Strict immutable base for values crossing an Agent UI surface boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RunModelOverrides(SurfaceModel):
    """Per-operation choices; never rewrite resources or sticky Thread heads."""

    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    thinking: bool | Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


class WorkspaceContext(SurfaceModel):
    directory: str
    project_id: str


class ContextUsageView(SurfaceModel):
    thread_id: str
    latest_request_tokens: int | None = None
    context_window: int | None = None
    model_id: str | None = None
    thinking: str | bool | None = None


class AgentSourceView(SurfaceModel):
    kind: Literal["agent", "markdown"]
    id: str = Field(min_length=1, max_length=128)

    @classmethod
    def from_stored(cls, source: AgentResourceSource | MarkdownSubagentSource) -> Self:
        return cls(kind=source.kind, id=source.id)


class NewThreadDefaults(SurfaceModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    environment_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None


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
    metadata: ContentMetadata = Field(default_factory=ContentMetadata)

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
    continuation_id: str | None = Field(default=None, pattern=r"^(?:initial:)?[0-9a-f]{64}$")
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


class TaskView(SurfaceModel):
    task_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=512)
    active_form: str | None = Field(default=None, max_length=512)
    status: Literal["pending", "in_progress", "completed"]
    owner: str | None = Field(default=None, max_length=256)
    blocks: tuple[str, ...] = Field(default=(), max_length=256)
    blocked_by: tuple[str, ...] = Field(default=(), max_length=256)


class TaskPage(SurfaceModel):
    continuation_id: str | None = Field(default=None, pattern=r"^(?:initial:)?[0-9a-f]{64}$")
    version: int | None = Field(default=None, ge=1)
    tasks: tuple[TaskView, ...] = Field(default=(), max_length=256)
    total: int = Field(default=0, ge=0)
    omitted: int = Field(default=0, ge=0)
    available: bool = True


class ThreadFocusSnapshot(SurfaceModel):
    epoch: str = Field(min_length=1, max_length=80)
    cutover_sequence: int = Field(ge=0)
    thread: ThreadDetail
    root_operation: RootOperationView | None = None
    children: ChildExecutionPage
    tasks: TaskPage = Field(default_factory=TaskPage)
    recent_events: tuple[LiveEvent, ...] = ()


class LaunchProjectSelected(SurfaceModel):
    kind: Literal["selected"] = "selected"
    directory: str = Field(min_length=1, max_length=4096)
    project: ProjectSummary
    configuration_path: str | None = Field(default=None, max_length=4096)


class LaunchProjectUnmatched(SurfaceModel):
    kind: Literal["unmatched"] = "unmatched"
    directory: str = Field(min_length=1, max_length=4096)
    configuration_path: str | None = Field(default=None, max_length=4096)


class LaunchProjectAmbiguous(SurfaceModel):
    kind: Literal["ambiguous"] = "ambiguous"
    directory: str = Field(min_length=1, max_length=4096)
    projects: tuple[ProjectSummary, ...] = Field(min_length=2, max_length=64)
    configuration_path: str | None = Field(default=None, max_length=4096)


type LaunchProjectResolution = Annotated[
    LaunchProjectSelected | LaunchProjectUnmatched | LaunchProjectAmbiguous,
    Field(discriminator="kind"),
]


class PendingDecisionSummary(SurfaceModel):
    kind: Literal["question", "approval", "external", "mixed"]
    count: int = Field(ge=1, le=256)


class ActiveWorkSummary(SurfaceModel):
    root_operations: int = Field(default=0, ge=0)
    child_executions: int = Field(default=0, ge=0)


class ChildStatusCounts(SurfaceModel):
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    lost: int = Field(default=0, ge=0)
    active: int = Field(default=0, ge=0)
    unavailable: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _running_partition(self) -> Self:
        if self.active + self.unavailable != self.running:
            raise ValueError("active and unavailable children must partition running children")
        return self


class ActivitySummary(SurfaceModel):
    kind: Literal["assistant", "reasoning", "tool", "child", "decision", "failure", "notice"]
    text: str = Field(min_length=1, max_length=2048)
    occurred_at: datetime | None = None

    @field_validator("occurred_at")
    @classmethod
    def _optional_activity_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Activity timestamp must include a UTC offset")
        return value.astimezone(UTC)


class ThreadActivityView(SurfaceModel):
    thread: ThreadSummary
    project_name: str = Field(min_length=1, max_length=256)
    agent_name: str = Field(min_length=1, max_length=256)
    environment_name: str = Field(min_length=1, max_length=256)
    pending_decision: PendingDecisionSummary | None = None
    latest_operation: RootOperationView | None = None
    children: ChildStatusCounts = Field(default_factory=ChildStatusCounts)
    latest_activity: ActivitySummary | None = None
    available_actions: tuple[Literal["open", "archive", "respond", "wait", "steer", "cancel"], ...] = ("open",)


class ThreadActivityPage(SurfaceModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    rows: tuple[ThreadActivityView, ...]
    total: int = Field(ge=0)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class AgentSummary(SurfaceModel):
    agent_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=4096)


class SelectableResourceSummary(SurfaceModel):
    resource_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    kind: Literal["harness_plugin", "environment_run_extension", "mcp_server"]
    implementation_key: str | None = Field(default=None, min_length=1, max_length=200)
    source_path: str = Field(min_length=1, max_length=4096)


class ThreadSelectorCatalog(SurfaceModel):
    agents: tuple[AgentSummary, ...]
    environments: tuple[EnvironmentProfileSummary, ...]
    harness_plugins: tuple[SelectableResourceSummary, ...]
    environment_run_extensions: tuple[SelectableResourceSummary, ...]
    mcp_servers: tuple[SelectableResourceSummary, ...]


class ThreadConfigurationPatch(SurfaceModel):
    agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    environment_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _non_empty_and_non_null(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Thread configuration patch must not be empty")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null when supplied")
        return self


class ThreadConfigurationMutationInput(SurfaceModel):
    expected_version: int = Field(ge=1)
    patch: ThreadConfigurationPatch


class ProjectPathCompletion(SurfaceModel):
    project_id: str = Field(min_length=1, max_length=128)
    mount: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=4096)
    kind: Literal["file", "directory"]
    display: str = Field(min_length=1, max_length=8192)


class ProjectPathCompletionPage(SurfaceModel):
    project_id: str = Field(min_length=1, max_length=128)
    query: str = Field(default="", max_length=512)
    items: tuple[ProjectPathCompletion, ...] = Field(max_length=100)
    truncated: bool = False


class SkillCatalogItemView(SurfaceModel):
    item_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=16 * 1024)
    source_id: str = Field(min_length=1, max_length=256)
    logical_path: str = Field(min_length=1, max_length=4096)


class SkillCatalogView(SurfaceModel):
    catalog_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_kind: Literal["draft", "idle", "active"]
    thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    receipt_id: str | None = Field(default=None, min_length=1, max_length=128)
    items: tuple[SkillCatalogItemView, ...] = Field(max_length=512)


class SkillReference(SurfaceModel):
    catalog_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=256)


class QuestionOptionView(SurfaceModel):
    label: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4096)


class QuestionView(SurfaceModel):
    question: str = Field(min_length=1, max_length=8192)
    header: str = Field(min_length=1, max_length=12)
    options: tuple[QuestionOptionView, ...] = Field(min_length=2, max_length=4)
    multi_select: bool = False


class StructuredQuestionRequestView(SurfaceModel):
    request_id: str = Field(min_length=1, max_length=256)
    kind: Literal["question"] = "question"
    tool_name: str = Field(min_length=1, max_length=128)
    questions: tuple[QuestionView, ...] = Field(min_length=1, max_length=4)
    metadata: dict[str, JsonValue] | None = None
    metadata_omitted: bool = False


class ApprovalRequestView(SurfaceModel):
    request_id: str = Field(min_length=1, max_length=256)
    kind: Literal["approval"] = "approval"
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: JsonValue | None = None
    arguments_omitted: bool = False
    metadata: dict[str, JsonValue] | None = None
    metadata_omitted: bool = False
    override_allowed: bool = True


class ExternalRequestView(SurfaceModel):
    request_id: str = Field(min_length=1, max_length=256)
    kind: Literal["external"] = "external"
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: JsonValue | None = None
    arguments_omitted: bool = False
    metadata: dict[str, JsonValue] | None = None
    metadata_omitted: bool = False


type DecisionRequestView = Annotated[
    StructuredQuestionRequestView | ApprovalRequestView | ExternalRequestView,
    Field(discriminator="kind"),
]


class DecisionBatchView(SurfaceModel):
    continuation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    requests: tuple[DecisionRequestView, ...] = Field(min_length=1, max_length=256)


class QuestionResponse(SurfaceModel):
    kind: Literal["question"] = "question"
    request_id: str = Field(min_length=1, max_length=256)
    answers: dict[str, str | tuple[str, ...]] = Field(default_factory=dict)
    response: str | None = Field(default=None, min_length=1, max_length=64 * 1024)


class DecisionResponseBatch(SurfaceModel):
    expected_continuation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    responses: tuple[ApprovalDecision | ExternalToolResult | QuestionResponse, ...] = Field(
        min_length=1,
        max_length=256,
    )

    @model_validator(mode="after")
    def _unique_requests(self) -> Self:
        request_ids = tuple(item.request_id for item in self.responses)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Decision response request IDs must be unique")
        if len(self.model_dump_json().encode("utf-8")) > _MAX_DEFERRED_RESPONSE_BYTES:
            raise ValueError("Decision response exceeds the surface payload limit")
        return self


class ReviewView(SurfaceModel):
    lifecycle: Literal["pending", "running", "closed", "unavailable"]
    kind: Literal["json", "shell", "task", "child", "diff", "generic"]
    title: str = Field(min_length=1, max_length=512)
    summary: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=256 * 1024)
    value: JsonValue | None = None
    truncated: bool = False
    omitted: bool = False
    unavailable_reason: str | None = Field(default=None, max_length=2048)


__all__ = [
    "ActiveWorkSummary",
    "ActivitySummary",
    "AgentSourceView",
    "AgentSummary",
    "ApprovalDecision",
    "ApprovalRequestView",
    "ChildActivityView",
    "ChildControlResult",
    "ChildExecutionPage",
    "ChildExecutionView",
    "ChildStatusCounts",
    "ChildToolCallView",
    "ContinuationSelectionView",
    "DecisionBatchView",
    "DecisionRequestView",
    "DecisionResponseBatch",
    "DeferredRequestView",
    "DeferredResponseItem",
    "EnvironmentOutcomeView",
    "EnvironmentProfileSummary",
    "ExternalRequestView",
    "ExternalToolResult",
    "FailureView",
    "LaunchProjectAmbiguous",
    "LaunchProjectResolution",
    "LaunchProjectSelected",
    "LaunchProjectUnmatched",
    "NewThreadDefaults",
    "PendingDecisionSummary",
    "ProjectPathCompletion",
    "ProjectPathCompletionPage",
    "ProjectSummary",
    "QuestionOptionView",
    "QuestionResponse",
    "QuestionView",
    "ReviewView",
    "RootActivityState",
    "RootActivityView",
    "RootControlResult",
    "RootExecutionView",
    "RootOperationStatus",
    "RootOperationView",
    "RootRunOutcomeView",
    "RootRunReceipt",
    "SelectableResourceSummary",
    "SkillCatalogItemView",
    "SkillCatalogView",
    "SkillReference",
    "StructuredQuestionRequestView",
    "SurfaceModel",
    "TaskPage",
    "TaskView",
    "ThreadActivityPage",
    "ThreadActivityView",
    "ThreadConfigurationMutationInput",
    "ThreadConfigurationPatch",
    "ThreadConfigurationView",
    "ThreadDeferredResponse",
    "ThreadDetail",
    "ThreadFocusSnapshot",
    "ThreadMetadataMutation",
    "ThreadMetadataPatch",
    "ThreadPage",
    "ThreadSelectorCatalog",
    "ThreadSummary",
    "TranscriptEntry",
    "TranscriptPage",
    "TranscriptPart",
]
