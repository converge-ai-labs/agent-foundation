"""Typed private Host/Runner execution values."""

from __future__ import annotations

from typing import Literal

from a13n_environment_provider import EnvironmentPauseMode
from pydantic import Field, JsonValue
from pydantic_ai.usage import RunUsage

from a13n_ui.environments import EnvironmentResourceStatus, SessionEnvironmentResource
from a13n_ui.sessions import SessionAgentSkillSelection
from a13n_ui.sessions.models import StrictModel
from a13n_ui.storage import ObjectRef


class SelectedEnvironmentResource(StrictModel):
    """One Host-selected resource row and its exact immutable state object."""

    resource: SessionEnvironmentResource
    provider_state: ObjectRef | None = None


class ExecuteRootRun(StrictModel):
    """Complete authority-neutral input for one Runner-owned root Run."""

    request_id: str = Field(pattern=r"^request-[0-9a-f]{32}$")
    generation_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    continuation: ObjectRef
    agent_snapshot: ObjectRef
    environment_snapshot: ObjectRef
    environment_resources: tuple[SelectedEnvironmentResource, ...]
    skill_selections: tuple[SessionAgentSkillSelection, ...]
    input_value: JsonValue | None = None
    deferred_results: JsonValue | None = None


class ExecuteEnvironmentCommand(StrictModel):
    """One exact Runner-owned Environment lifecycle command."""

    request_id: str = Field(pattern=r"^request-[0-9a-f]{32}$")
    generation_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    environment_snapshot: ObjectRef
    selected_resource: SelectedEnvironmentResource
    action: Literal["ensure_available", "pause", "destroy"]
    pause_mode: EnvironmentPauseMode | None = None


class ProviderStateUpdate(StrictModel):
    """Detached provider state requiring Host publication before acknowledgement."""

    update_id: str = Field(pattern=r"^update-[0-9a-f]{32}$")
    request_id: str = Field(pattern=r"^request-[0-9a-f]{32}$")
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    mount_name: str = Field(min_length=1, max_length=128)
    provider_key: str = Field(min_length=3, max_length=128)
    provider_spec_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_version: str | None = Field(default=None, min_length=1, max_length=64)
    provider_state: JsonValue | None = None
    status: EnvironmentResourceStatus


class RunnerAsyncWorkEvent(StrictModel):
    """Generation-local asynchronous completion routed from Runner to stable Host."""

    generation_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(pattern=r"^session-[0-9a-f]{16,64}$")
    parent_active: bool
    source: Literal["async_subagent", "background_process"]
    kind: Literal["completion", "gap"]
    thread_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=128)
    agent_instance_id: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=64)
    child_thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=64)
    usage: RunUsage = Field(default_factory=RunUsage)


class RunnerContinuationCandidate(StrictModel):
    """Complete Harness outcome returned for Host publication and selection."""

    run_id: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "suspended", "failed", "cancelled"]
    output: JsonValue | None = None
    failure: JsonValue | None = None
    harness_state: JsonValue | None = None
    deferred_requests: JsonValue | None = None


class RunnerEnvironmentResult(StrictModel):
    """Terminal result for one Runner-owned Environment lifecycle command."""

    request_id: str = Field(pattern=r"^request-[0-9a-f]{32}$")
    failure: JsonValue | None = None


class RunnerRunResult(StrictModel):
    """Terminal Runner execution result after runtime collaborators close."""

    request_id: str = Field(pattern=r"^request-[0-9a-f]{32}$")
    candidate: RunnerContinuationCandidate | None = None
    failure: JsonValue | None = None
    cleanup_failure: JsonValue | None = None


__all__ = [
    "ExecuteEnvironmentCommand",
    "ExecuteRootRun",
    "ProviderStateUpdate",
    "RunnerAsyncWorkEvent",
    "RunnerContinuationCandidate",
    "RunnerEnvironmentResult",
    "RunnerRunResult",
    "SelectedEnvironmentResource",
]
