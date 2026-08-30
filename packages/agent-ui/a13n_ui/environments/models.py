"""Detached Session Environment resource values."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue, field_validator

from a13n_ui.sessions.models import StrictModel

_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EnvironmentResourceStatus(StrEnum):
    unprovisioned = "unprovisioned"
    available = "available"
    paused = "paused"
    unavailable = "unavailable"


class SessionEnvironmentResource(StrictModel):
    session_id: str = Field(min_length=1, max_length=80)
    mount_name: str = Field(min_length=1, max_length=128)
    model_alias: str = Field(min_length=1, max_length=63)
    permission_ceiling: frozenset[str]
    provider_key: str = Field(min_length=3, max_length=128)
    provider_schema_version: str = Field(min_length=1, max_length=64)
    provider_spec_digest: _DIGEST
    resource_allocation: str = Field(min_length=1, max_length=32)
    status: EnvironmentResourceStatus
    provider_state_digest: _DIGEST | None = None
    failure: JsonValue | None = None
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StoredProviderState(StrictModel):
    session_id: str = Field(min_length=1, max_length=80)
    mount_name: str = Field(min_length=1, max_length=128)
    provider_key: str = Field(min_length=3, max_length=128)
    provider_spec_digest: _DIGEST
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
    resources: tuple[SessionEnvironmentResource, ...]
    ready: bool


__all__ = [
    "EnvironmentAvailability",
    "EnvironmentResourceStatus",
    "SessionEnvironmentResource",
    "StoredProviderState",
]
