"""Typed task context carried by the ordinary accepted analysis input."""

from typing import Literal

from pydantic import BaseModel, JsonValue

from a13n_service.runs.findings.schemas import Preset, SelectedTrace


class Target(SelectedTrace):
    agent_id: str
    agent_revision_id: str | None
    agent_name: str
    agent_url: str
    run_url: str
    trace_url: str


class AnalysisInput(BaseModel):
    kind: Literal["finding_analysis"] = "finding_analysis"
    analysis_id: str
    targets: list[Target]
    presets: dict[Preset, str]
    requirements: str
    selection_truncated: bool
    feedback: dict[str, JsonValue]
