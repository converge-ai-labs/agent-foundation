"""Harness-owned mounts, aggregate readiness and path correlation."""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from a13n_environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MOUNT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _require_identifier(value: str, name: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def _positive_finite(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def _require_mount_name(value: str) -> str:
    if not _MOUNT_NAME_PATTERN.fullmatch(value):
        raise ValueError("mount name must match ^[a-z][a-z0-9-]{0,62}$")
    return value


class EnvironmentMountInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    provider_type: str
    descriptor: EnvironmentDescriptor
    permission_ceiling: EnvironmentPermissionSet
    default_working_directory: str | None
    mount_path: str | None = None
    provider_root: str = "/"

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return _require_mount_name(value)

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, value: str) -> str:
        return _require_identifier(value, "provider_type")


class EnvironmentMountObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    mount: EnvironmentMountInfo
    availability: EnvironmentAvailability


class EnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    mounts: tuple[EnvironmentMountInfo, ...]
    default_mount: str | None

    @field_validator("default_mount")
    @classmethod
    def _valid_default_mount(cls, value: str | None) -> str | None:
        return None if value is None else _require_mount_name(value)


class EnvironmentReadinessRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: frozenset[EnvironmentOperationFamily]
    mounts: frozenset[str] | None = None
    timeout_seconds: float | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> EnvironmentReadinessRequirement:
        if not self.operations:
            raise ValueError("operations must not be empty")
        if self.mounts is not None and not self.mounts:
            raise ValueError("mounts must not be empty")
        if self.timeout_seconds is not None:
            _positive_finite(self.timeout_seconds, "timeout_seconds")
        return self


class EnvironmentPath(BaseModel):
    model_config = ConfigDict(frozen=True)

    mount_id: str
    execution_id: str
    path: str

    @field_validator("mount_id", "execution_id")
    @classmethod
    def _valid_execution_id(cls, value: str) -> str:
        return _require_identifier(value, "execution_id")


class EnvironmentChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: Annotated[int, Field(gt=0)]
    kind: Literal["mounted", "replaced", "unmounted", "default_changed"]
    name: str | None = None
    previous_default: str | None = None
    current_default: str | None = None

    @field_validator("name", "previous_default", "current_default")
    @classmethod
    def _valid_mount_name(cls, value: str | None) -> str | None:
        return None if value is None else _require_mount_name(value)
