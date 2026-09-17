"""Typed detached contracts for Harness UI durable heads and immutable payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from a13n_environment import EnvironmentState
from a13n_harness import HarnessState, SafeFailure
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic_ai.tools import DeferredToolRequests

from a13n_harness_ui.conversation import ConversationExcerpt
from a13n_harness_ui.display_history import DisplayHistory, saved_display_history

from .objects import ObjectKind, ObjectRef

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type ExecutionStatus = Literal["running", "succeeded", "failed", "cancelled", "lost"]


class StoredContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AgentResourceSource(StoredContract):
    kind: Literal["agent"] = "agent"
    id: str = Field(min_length=1, max_length=128)


class MarkdownSubagentSource(StoredContract):
    kind: Literal["markdown"] = "markdown"
    id: str = Field(min_length=1, max_length=128)


type AgentSource = AgentResourceSource | MarkdownSubagentSource


class ThreadConfiguration(StoredContract):
    version: int = Field(ge=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    agent_source: AgentSource
    environment_profile_id: str = Field(min_length=1, max_length=128)
    harness_plugin_ids: tuple[str, ...] = ()
    environment_run_extension_ids: tuple[str, ...] = ()
    mcp_server_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_lists(self) -> Self:
        for values in (
            self.harness_plugin_ids,
            self.environment_run_extension_ids,
            self.mcp_server_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Thread configuration lists must be unique and ordered")
        return self


class ThreadConfigurationPatch(StoredContract):
    """Patch whose ``model_fields_set`` distinguishes omitted axes."""

    project_id: str | None = None
    agent_source: AgentSource | None = None
    environment_profile_id: str | None = None
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _set_fields_are_not_null(self) -> Self:
        for name in self.model_fields_set:
            if name != "project_id" and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null when supplied")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.model_fields_set

    def apply(self, current: ThreadConfiguration) -> ThreadConfiguration:
        updates: dict[str, object] = {}
        for name in self.model_fields_set:
            updates[name] = getattr(self, name)
        if not updates:
            return current
        updates["version"] = current.version + 1
        return current.model_copy(update=updates)


class ThreadConfigurationMutation(StoredContract):
    expected_version: int = Field(ge=1)
    patch: ThreadConfigurationPatch

    @model_validator(mode="after")
    def _non_empty_patch(self) -> Self:
        if self.patch.is_empty:
            raise ValueError("Thread configuration mutation patch must not be empty")
        return self


class ThreadCompletion(StoredContract):
    """Latest successfully selected root result; independent of current operation state."""

    version: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=80)
    continuation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_at: datetime


class Thread(StoredContract):
    thread_id: str = Field(min_length=1, max_length=80)
    parent_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime
    metadata_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=512)
    excerpt: ConversationExcerpt = Field(default_factory=ConversationExcerpt)
    activity_at: datetime | None = None
    touched_at: datetime | None = None
    archived: bool = False
    configuration: ThreadConfiguration
    initial_state: ObjectRef
    continuation: ObjectRef | None = None
    completion: ThreadCompletion | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Thread timestamps must include a UTC offset")
        return value.astimezone(UTC)


class StoredThreadInitialState(StoredContract):
    schema_version: Literal["1"] = "1"
    harness_state: HarnessState
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)


class StoredContinuation(StoredContract):
    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    run_composition: ObjectRef
    harness_state: HarnessState
    excerpt: ConversationExcerpt = Field(default_factory=ConversationExcerpt)
    deferred_requests: DeferredToolRequests | None = None
    created_at: datetime

    @property
    def display_history(self) -> DisplayHistory | None:
        return saved_display_history(self.harness_state)

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _composition_kind(self) -> Self:
        if self.run_composition.object_kind is not ObjectKind.run_composition:
            raise ValueError("continuation must reference a Run composition")
        return self


class CompactChildActivity(StoredContract):
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
    activities: tuple[CompactChildActivity, ...] = Field(default=(), max_length=512)
    # The latest completed answer is retained losslessly; transport reads are paged.
    final_answer: str | None = None


class StoredChildCheckpoint(StoredContract):
    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=80)
    child_thread_id: str = Field(min_length=1, max_length=80)
    child_run_id: str = Field(min_length=1, max_length=80)
    segment_index: int = Field(ge=0)
    run_composition: ObjectRef
    harness_state: HarnessState
    deferred_requests: DeferredToolRequests | None = None
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
        if self.run_composition.object_kind is not ObjectKind.run_composition:
            raise ValueError("checkpoint must reference a Run composition")
        return self


class ChildExecutionHead(StoredContract):
    execution_id: str
    parent_thread_id: str
    child_thread_id: str
    child_run_id: str
    segment_index: int
    run_composition: ObjectRef
    status: ExecutionStatus
    selected_checkpoint: ObjectRef | None
    resumed_from: str | None
    failure: SafeFailure | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @field_validator("created_at", "updated_at", "completed_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("child execution timestamps must include a UTC offset")
        return value.astimezone(UTC)

    @property
    def resumable(self) -> bool:
        return self.status == "succeeded" and self.selected_checkpoint is not None


class EnvironmentBindingKey(StoredContract):
    thread_id: str = Field(min_length=1, max_length=80)
    environment_profile_id: str = Field(min_length=1, max_length=128)
    profile_digest: Digest
    adapter_key: str = Field(min_length=1, max_length=200)
    normalized_root: str = Field(min_length=1, max_length=4096)


class StoredEnvironmentState(StoredContract):
    schema_version: Literal["1"] = "1"
    binding: EnvironmentBindingKey
    provider_schema_version: str = Field(min_length=1, max_length=64)
    state: EnvironmentState
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)


class EnvironmentBindingHead(StoredContract):
    key: EnvironmentBindingKey
    state: ObjectRef | None
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Environment binding updated_at must include a UTC offset")
        return value.astimezone(UTC)


class ResourceIndexEntry(StoredContract):
    generation_digest: Digest
    resource_kind: str
    resource_id: str
    name: str
    relative_path: str
    source_digest: Digest
    normalized_digest: Digest


__all__ = [
    "AgentResourceSource",
    "AgentSource",
    "ChildExecutionHead",
    "CompactChildActivity",
    "CompactChildDisplay",
    "EnvironmentBindingHead",
    "EnvironmentBindingKey",
    "ExecutionStatus",
    "MarkdownSubagentSource",
    "ResourceIndexEntry",
    "SafeFailure",
    "StoredChildCheckpoint",
    "StoredContinuation",
    "StoredEnvironmentState",
    "StoredThreadInitialState",
    "Thread",
    "ThreadConfiguration",
    "ThreadConfigurationMutation",
    "ThreadConfigurationPatch",
]
