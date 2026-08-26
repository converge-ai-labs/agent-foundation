from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ._json import JsonBoundaryError, detach_json, detach_json_object

_BOUNDED_ID = Annotated[str, Field(min_length=1, max_length=128)]
_PROVIDER_KEY = Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")]
_SCHEMA_VERSION = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class EnvironmentProviderSpec(BaseModel):
    """Credential-free desired configuration for one Environment provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: _PROVIDER_KEY
    schema_version: _SCHEMA_VERSION
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("parameters", mode="before")
    @classmethod
    def _detached_parameters(cls, value: object) -> dict[str, JsonValue]:
        try:
            return detach_json_object(value)
        except JsonBoundaryError as exc:
            raise ValueError(str(exc)) from exc


class EnvironmentProviderResourceState(BaseModel):
    """Provider-owned serializable state for one managed resource."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: _PROVIDER_KEY
    state_version: _SCHEMA_VERSION
    data: JsonValue

    @field_validator("data", mode="before")
    @classmethod
    def _detached_data(cls, value: object) -> JsonValue:
        try:
            return detach_json(value)
        except JsonBoundaryError as exc:
            raise ValueError(str(exc)) from exc


class EnvironmentPauseMode(StrEnum):
    FULL = "full"
    FILESYSTEM = "filesystem"


class EnvironmentResourceAllocation(StrEnum):
    SINGLE_FROM_SPEC = "single_from_spec"
    MULTIPLE_FROM_SPEC = "multiple_from_spec"


class EnvironmentAttachmentConcurrency(StrEnum):
    SINGLE = "single"
    SHARED = "shared"


class EnvironmentLifecycleCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pause_modes: frozenset[EnvironmentPauseMode] = frozenset()
    resource_allocation: EnvironmentResourceAllocation
    attachment_concurrency: EnvironmentAttachmentConcurrency


class EnvironmentManagementAction(StrEnum):
    CREATE = "create"
    RESUME = "resume"
    PAUSE = "pause"
    DESTROY = "destroy"


class EnvironmentOperationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: _BOUNDED_ID
    action: EnvironmentManagementAction
    resource_correlation: _BOUNDED_ID
    attempt: Annotated[int, Field(ge=1, le=1_000_000)]

    @field_validator("operation_id", "resource_correlation")
    @classmethod
    def _non_blank_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("identifier must not contain surrounding whitespace")
        return value


class EnvironmentReconciliationPhase(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class EnvironmentReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: _BOUNDED_ID
    phase: EnvironmentReconciliationPhase
    state: EnvironmentProviderResourceState | None = None
    evidence: JsonValue | None = None

    @field_validator("operation_id")
    @classmethod
    def _non_blank_operation_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("operation_id must not contain surrounding whitespace")
        return value

    @field_validator("evidence", mode="before")
    @classmethod
    def _detached_evidence(cls, value: object) -> JsonValue | None:
        if value is None:
            return None
        try:
            return detach_json(value)
        except JsonBoundaryError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _phase_state_consistency(self) -> EnvironmentReconciliationResult:
        has_state = self.state is not None
        if self.phase in {EnvironmentReconciliationPhase.RUNNING, EnvironmentReconciliationPhase.PAUSED}:
            if not has_state:
                raise ValueError("running and paused reconciliation results require state")
        elif has_state:
            raise ValueError("absent and unknown reconciliation results cannot include state")
        return self
