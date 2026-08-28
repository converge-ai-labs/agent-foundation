"""Detached Environment resource, assignment, and provider-state values."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator

from a13n_ui.sessions.models import StrictModel

_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_RESOURCE_ID = Annotated[str, Field(pattern=r"^resource-[0-9a-f]{16,64}$")]
_ASSIGNMENT_ID = Annotated[str, Field(pattern=r"^assignment-[0-9a-f]{16,64}$")]


class HostResourceLifecycleState(StrEnum):
    unprovisioned = "unprovisioned"
    creating = "creating"
    available = "available"
    pausing = "pausing"
    paused = "paused"
    resuming = "resuming"
    destroying = "destroying"
    destroyed = "destroyed"
    missing = "missing"
    unknown = "unknown"
    failed = "failed"


class ProviderStateRef(StrictModel):
    object_digest: _DIGEST
    provider_key: str = Field(min_length=3, max_length=128)
    state_version: str = Field(min_length=1, max_length=64)
    host_resource_id: _RESOURCE_ID
    operation_fence: int = Field(gt=0)


class EnvironmentOperationView(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    action: Literal["create", "resume", "pause", "destroy"]
    attempt: int = Field(ge=1, le=1_000_000)


class HostEnvironmentResource(StrictModel):
    host_resource_id: _RESOURCE_ID
    provider_key: str = Field(min_length=3, max_length=128)
    provider_schema_version: str = Field(min_length=1, max_length=64)
    provider_spec_digest: _DIGEST
    resource_allocation: Literal["single_from_spec", "multiple_from_spec"]
    lifecycle_state: HostResourceLifecycleState
    operation_fence: int = Field(ge=0)
    selected_provider_state_digest: _DIGEST | None = None
    last_operation: EnvironmentOperationView | None = None
    failure: JsonValue | None = None
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class SessionEnvironmentAssignment(StrictModel):
    assignment_id: _ASSIGNMENT_ID
    session_id: str = Field(min_length=1, max_length=80)
    binding_name: str = Field(min_length=1, max_length=128)
    model_alias: str = Field(min_length=1, max_length=63)
    permission_ceiling: frozenset[str]
    required: bool
    scope_key: str = Field(min_length=1, max_length=128)
    host_resource_id: _RESOURCE_ID
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StoredProviderState(StrictModel):
    host_resource_id: _RESOURCE_ID
    provider_key: str = Field(min_length=3, max_length=128)
    provider_spec_digest: _DIGEST
    operation_fence: int = Field(gt=0)
    state_version: str = Field(min_length=1, max_length=64)
    provider_state: JsonValue
    exported_at: datetime

    @field_validator("exported_at")
    @classmethod
    def _normalize_exported_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class EnvironmentAvailability(StrictModel):
    session_id: str = Field(min_length=1, max_length=80)
    assignments: tuple[SessionEnvironmentAssignment, ...]
    resources: tuple[HostEnvironmentResource, ...]
    ready: bool


__all__ = [
    "EnvironmentAvailability",
    "EnvironmentOperationView",
    "HostEnvironmentResource",
    "HostResourceLifecycleState",
    "ProviderStateRef",
    "SessionEnvironmentAssignment",
    "StoredProviderState",
]
