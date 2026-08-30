"""Detached runtime-generation observations exposed by AgentUiHost."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RuntimeGenerationState(StrEnum):
    """Observable lifecycle state for one runtime Runner generation."""

    starting = "starting"
    ready = "ready"
    active = "active"
    draining = "draining"
    exited = "exited"


class RuntimeExitReason(StrEnum):
    """Bounded reason for one observed Runner exit."""

    graceful = "graceful"
    startup_failed = "startup_failed"
    unexpected = "unexpected"
    terminated = "terminated"
    killed = "killed"


class RuntimeReadiness(BaseModel):
    """Agent UI-owned readiness reported separately from process control."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    protocol_version: str = Field(min_length=1, max_length=32)
    agent_ui_version: str = Field(min_length=1, max_length=64)
    python_version: str = Field(min_length=1, max_length=64)
    loaded_provenance: tuple[str, ...] = Field(max_length=64)


class RuntimeGenerationObservation(BaseModel):
    """Safe immutable observation for one Runner generation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation_id: str = Field(min_length=1, max_length=64)
    process_id: int | None = Field(default=None, gt=0)
    state: RuntimeGenerationState
    started_at: datetime
    updated_at: datetime
    readiness: RuntimeReadiness | None = None
    exit_reason: RuntimeExitReason | None = None
    return_code: int | None = None


class RuntimeDiagnostic(BaseModel):
    """Bounded path-free runtime lifecycle diagnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation_id: str | None = Field(default=None, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    detail: str = Field(min_length=1, max_length=512)
    observed_at: datetime


class RuntimeStatus(BaseModel):
    """Current Host runtime routing and retained observations."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    active_generation_id: str | None = Field(default=None, max_length=64)
    candidate_generation_id: str | None = Field(default=None, max_length=64)
    generations: tuple[RuntimeGenerationObservation, ...] = Field(max_length=32)
    diagnostics: tuple[RuntimeDiagnostic, ...] = Field(max_length=100)


class RuntimeRestartResult(BaseModel):
    """Result of one serialized runtime restart command."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    previous_generation_id: str | None = Field(default=None, max_length=64)
    active: RuntimeGenerationObservation


__all__ = [
    "RuntimeDiagnostic",
    "RuntimeExitReason",
    "RuntimeGenerationObservation",
    "RuntimeGenerationState",
    "RuntimeReadiness",
    "RuntimeRestartResult",
    "RuntimeStatus",
]
