"""Organization requests compose a target, a model and task instructions."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.resources.memories.schemas import OrganizationHistory, OrganizationPolicy
from a13n_service.runs.schemas import SpaceOrganizationStatus


class FileMemoryTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["memory"] = "memory"
    id: str = Field(min_length=1, max_length=72)


class SpaceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["space"] = "space"
    id: str = Field(min_length=1, max_length=72)


type OrganizationTarget = Annotated[FileMemoryTarget | SpaceTarget, Field(discriminator="kind")]


class OrganizationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    thread_id: str
    run_id: str
    sealed_at: datetime


class OrganizationSourcePage(BaseModel):
    items: list[OrganizationSource]
    next_offset: int | None = None


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: OrganizationTarget
    model: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="Organize the saved memory files.", min_length=1, max_length=16384)
    history: OrganizationHistory | None = None
    output: Literal["direct", "candidate"] = "direct"


class MemoryOrganization(BaseModel):
    id: str
    workspace_id: str
    request: OrganizationRequest
    sources: list[OrganizationSource]
    candidate_memory_id: str | None = None
    session_id: str
    thread_id: str
    run_id: str
    created_by_id: str
    created_at: datetime


class CandidateApply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["checked", "replace_all"] = "checked"
    candidate_seq: int = Field(ge=0)
    target_seq: int = Field(ge=0)


class CandidateChange(BaseModel):
    path: str
    operation: Literal["create", "update", "delete"]


class CandidateApplication(BaseModel):
    request: CandidateApply
    result_seq: int
    applied_by_id: str
    applied_at: datetime


class OrganizationCandidate(BaseModel):
    organization_id: str
    memory_id: str
    target_memory_id: str
    status: Literal["generating", "ready", "failed", "applied", "discarded", "unavailable"]
    baseline_seq: int
    candidate_seq: int | None
    target_seq: int | None
    changes: list[CandidateChange]
    files: list[str]
    application: CandidateApplication | None = None


class CandidateFile(BaseModel):
    path: str
    candidate_seq: int
    baseline: str | None
    candidate: str | None


class CandidateReplacement(BaseModel):
    candidate_seq: int
    target_seq: int
    deletions: list[str]
    overwrites: list[str]
    restorations: list[str]


class CandidateDiff(BaseModel):
    path: str
    diff: str
    truncated: bool


class MemoryOrganizationPage(BaseModel):
    items: list[MemoryOrganization]
    next_cursor: str | None = None


class MemoryAutomation(BaseModel):
    id: str
    policy: OrganizationPolicy
    status: SpaceOrganizationStatus
    processed_history_runs: int
    version: int
