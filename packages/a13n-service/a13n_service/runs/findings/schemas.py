"""Bounded finding submissions and on-demand analysis commands."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.infra.ids import ObjectId

type Severity = Literal["critical", "warning", "suggestion"]
type Assessment = Literal["unreviewed", "confirmed", "expected", "insufficient"]
type Preset = Literal["execution", "recovery", "answer"]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Input):
    run_id: ObjectId
    trace_id: TraceId
    span_ids: list[SpanId] = Field(default_factory=list, max_length=20)


class FindingCreate(_Input):
    agent_id: ObjectId
    agent_revision_id: ObjectId
    title: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=64)
    severity: Severity = "warning"
    explanation: str = Field(min_length=1, max_length=8192)
    suggestion: str = Field(min_length=1, max_length=8192)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    limitations: str = Field(default="", max_length=4096)
    # Stable caller-supplied identity for retry-safe submission; it never changes a reviewed finding.
    source_key: str = Field(min_length=1, max_length=128)


class FindingUpdate(_Input):
    assessment: Assessment | None = None
    closed: bool | None = None


class Finding(FindingCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    analysis_id: str | None
    source_run_id: str | None
    created_by_id: str
    assessment: Assessment
    closed: bool
    version: int
    created_at: datetime
    updated_at: datetime


class FindingPage(BaseModel):
    items: list[Finding]
    next_cursor: str | None


class AnalysisCreate(_Input):
    agent_id: ObjectId | None = None
    trace_id: TraceId | None = None
    started_after: AwareDatetime | None = None
    started_before: AwareDatetime | None = None
    presets: list[Preset] = Field(
        default_factory=lambda: ["execution", "recovery", "answer"], min_length=1, max_length=3
    )
    max_traces: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def selection(self) -> Self:
        if self.trace_id is not None and (self.started_after is not None or self.started_before is not None):
            raise ValueError("Choose one trace or a time range")
        if len(self.presets) != len(set(self.presets)):
            raise ValueError("Presets must be unique")
        return self


class SelectedTrace(_Input):
    trace_id: TraceId
    run_id: ObjectId


class Analysis(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str | None
    session_id: str
    thread_id: str
    run_id: str
    run_status: str
    selection: AnalysisCreate
    selected_traces: list[SelectedTrace] = Field(min_length=1, max_length=20)
    selection_truncated: bool
    read_trace_ids: list[str]
    finding_count: int
    cited_trace_count: int
    created_at: datetime


class AnalysisPage(BaseModel):
    items: list[Analysis]
    next_cursor: str | None
