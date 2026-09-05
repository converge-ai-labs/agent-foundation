"""Immutable provider-neutral Environment values and failures."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator, model_validator

from ._json import JsonBoundaryError, detach_json

type EnvironmentOperationFamily = Literal["files", "shell", "processes", "ports", "outputs", "state"]
ENVIRONMENT_OPERATION_FAMILIES = frozenset({"files", "shell", "processes", "ports", "outputs", "state"})
ENVIRONMENT_ACTION_CATALOG_VERSION = "environment-actions/2"
DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS = 600.0
DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS = 600.0

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MOUNT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class EnvironmentError(Exception):
    """Stable provider-neutral Environment operation failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retry_hint: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.code = code
        self.retry_hint = retry_hint
        self.details = dict(details or {})
        super().__init__(message)


class EnvironmentAction(StrEnum):
    """Exact authorization values in the locked first-party catalog."""

    FILE_STAT = "environment.file.stat"
    FILE_READ_TEXT = "environment.file.read_text"
    FILE_READ_BYTES = "environment.file.read_bytes"
    FILE_WRITE_TEXT = "environment.file.write_text"
    FILE_PATCH_TEXT = "environment.file.patch_text"
    FILE_LIST = "environment.file.list"
    FILE_QUERY = "environment.file.query"
    FILE_SEARCH_TEXT = "environment.file.search_text"
    FILE_MKDIR = "environment.file.mkdir"
    FILE_MOVE = "environment.file.move"
    FILE_REMOVE = "environment.file.remove"
    FILE_WRITE_BYTES = "environment.file.write_bytes"
    FILE_COPY_SOURCE = "environment.file.copy_source"
    FILE_COPY_DESTINATION = "environment.file.copy_destination"
    SHELL_EXEC = "environment.shell.exec"
    PROCESS_START = "environment.process.start"
    PROCESS_INSPECT = "environment.process.inspect"
    PROCESS_READ_OUTPUT = "environment.process.read_output"
    PROCESS_WRITE_STDIN = "environment.process.write_stdin"
    PROCESS_CLOSE_STDIN = "environment.process.close_stdin"
    PROCESS_SIGNAL = "environment.process.signal"
    PROCESS_WAIT = "environment.process.wait"
    PROCESS_KILL = "environment.process.kill"
    PROCESS_RELEASE = "environment.process.release"
    OUTPUT_READ = "environment.output.read"
    OUTPUT_RELEASE = "environment.output.release"
    PORT_INSPECT = "environment.port.inspect"
    PORT_WAIT = "environment.port.wait"
    STATE_EXPORT = "environment.state.export"
    STATE_RESTORE = "environment.state.restore"


@dataclass(frozen=True, slots=True)
class EnvironmentActionDispatch:
    family: EnvironmentOperationFamily
    facet: str
    method: str


ENVIRONMENT_ACTION_DISPATCH: Mapping[EnvironmentAction, EnvironmentActionDispatch] = MappingProxyType(
    {
        EnvironmentAction.FILE_STAT: EnvironmentActionDispatch("files", "files", "stat"),
        EnvironmentAction.FILE_READ_TEXT: EnvironmentActionDispatch("files", "files", "read_text"),
        EnvironmentAction.FILE_READ_BYTES: EnvironmentActionDispatch("files", "files", "read_bytes"),
        EnvironmentAction.FILE_WRITE_TEXT: EnvironmentActionDispatch("files", "files", "write_text"),
        EnvironmentAction.FILE_PATCH_TEXT: EnvironmentActionDispatch("files", "files", "patch_text"),
        EnvironmentAction.FILE_LIST: EnvironmentActionDispatch("files", "files", "list"),
        EnvironmentAction.FILE_QUERY: EnvironmentActionDispatch("files", "files", "query"),
        EnvironmentAction.FILE_SEARCH_TEXT: EnvironmentActionDispatch("files", "files", "search_text"),
        EnvironmentAction.FILE_MKDIR: EnvironmentActionDispatch("files", "files", "mkdir"),
        EnvironmentAction.FILE_MOVE: EnvironmentActionDispatch("files", "files", "move"),
        EnvironmentAction.FILE_REMOVE: EnvironmentActionDispatch("files", "files", "remove"),
        EnvironmentAction.FILE_WRITE_BYTES: EnvironmentActionDispatch("files", "files", "write_bytes_stream"),
        EnvironmentAction.FILE_COPY_SOURCE: EnvironmentActionDispatch("files", "files", "copy"),
        EnvironmentAction.FILE_COPY_DESTINATION: EnvironmentActionDispatch("files", "files", "copy"),
        EnvironmentAction.SHELL_EXEC: EnvironmentActionDispatch("shell", "shell", "exec"),
        EnvironmentAction.PROCESS_START: EnvironmentActionDispatch("processes", "processes", "start"),
        EnvironmentAction.PROCESS_INSPECT: EnvironmentActionDispatch("processes", "processes", "inspect"),
        EnvironmentAction.PROCESS_READ_OUTPUT: EnvironmentActionDispatch("processes", "processes", "read_output"),
        EnvironmentAction.PROCESS_WRITE_STDIN: EnvironmentActionDispatch("processes", "processes", "write_stdin"),
        EnvironmentAction.PROCESS_CLOSE_STDIN: EnvironmentActionDispatch("processes", "processes", "close_stdin"),
        EnvironmentAction.PROCESS_SIGNAL: EnvironmentActionDispatch("processes", "processes", "signal"),
        EnvironmentAction.PROCESS_WAIT: EnvironmentActionDispatch("processes", "processes", "wait"),
        EnvironmentAction.PROCESS_KILL: EnvironmentActionDispatch("processes", "processes", "kill"),
        EnvironmentAction.PROCESS_RELEASE: EnvironmentActionDispatch("processes", "processes", "release"),
        EnvironmentAction.OUTPUT_READ: EnvironmentActionDispatch("outputs", "outputs", "read"),
        EnvironmentAction.OUTPUT_RELEASE: EnvironmentActionDispatch("outputs", "outputs", "release"),
        EnvironmentAction.PORT_INSPECT: EnvironmentActionDispatch("ports", "ports", "inspect"),
        EnvironmentAction.PORT_WAIT: EnvironmentActionDispatch("ports", "ports", "wait"),
        EnvironmentAction.STATE_EXPORT: EnvironmentActionDispatch("state", "state", "export_state"),
        EnvironmentAction.STATE_RESTORE: EnvironmentActionDispatch("state", "state", "restore_state"),
    }
)


def _positive_finite(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def _require_identifier(value: str, name: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def _require_mount_name(value: str) -> str:
    if not _MOUNT_NAME_PATTERN.fullmatch(value):
        raise ValueError("mount name must match ^[a-z][a-z0-9-]{0,62}$")
    return value


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})  # type: ignore[return-value]
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)  # type: ignore[return-value]
    return value


def _thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json(item) for item in value]
    return value


class EnvironmentPermissionSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: frozenset[EnvironmentAction] = frozenset()


class EnvironmentMountDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    path: str
    read_only: bool

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return _require_identifier(value, "mount name")

    @field_validator("path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("mount path must be an absolute path")
        return value


class EnvironmentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    generation: str
    backing_identity: str | None = Field(default=None, min_length=1, max_length=256)
    operation_families: frozenset[EnvironmentOperationFamily]
    permissions: EnvironmentPermissionSet
    limits: Mapping[str, JsonValue] = Field(default_factory=dict)
    mounts: tuple[EnvironmentMountDescriptor, ...] = ()

    @field_validator("generation")
    @classmethod
    def _valid_generation(cls, value: str) -> str:
        return _require_identifier(value, "generation")

    @field_validator("limits", mode="after")
    @classmethod
    def _immutable_limits(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})

    @field_serializer("limits")
    def _serialize_limits(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return {key: _thaw_json(item) for key, item in value.items()}

    @model_validator(mode="after")
    def _actions_match_families(self) -> EnvironmentDescriptor:
        missing = {
            ENVIRONMENT_ACTION_DISPATCH[action].family
            for action in self.permissions.operations
            if ENVIRONMENT_ACTION_DISPATCH[action].family not in self.operation_families
        }
        if missing:
            raise ValueError(f"permissions advertise absent operation families: {sorted(missing)}")
        return self


class EnvironmentAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["available", "preparing", "degraded", "unavailable"]
    ready_families: frozenset[EnvironmentOperationFamily] = frozenset()
    reason_code: str | None = None

    @field_validator("reason_code")
    @classmethod
    def _valid_reason(cls, value: str | None) -> str | None:
        return None if value is None else _require_identifier(value, "reason_code")


class EnvironmentMountInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    provider_type: str
    descriptor: EnvironmentDescriptor
    permission_ceiling: EnvironmentPermissionSet
    default_working_directory: str | None
    mount_path: str | None = None

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


class EnvironmentProviderSpec(BaseModel):
    """Credential-free desired configuration for one Environment Provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")]
    schema_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    configuration: JsonValue

    @field_validator("configuration", mode="before")
    @classmethod
    def _finite_configuration(cls, value: object) -> JsonValue:
        try:
            return detach_json(value)
        except JsonBoundaryError as error:
            raise ValueError("configuration must be bounded finite JSON") from error


class EnvironmentState(BaseModel):
    """Provider-owned portable semantic soft reference for one target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")]
    state_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    state: JsonValue

    @field_validator("state", mode="before")
    @classmethod
    def _finite_state(cls, value: object) -> JsonValue:
        try:
            return detach_json(value)
        except JsonBoundaryError as error:
            raise ValueError("state must be bounded finite JSON") from error


class EnvironmentOperationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    mount_id: str
    observed_generation: str
    operation_id: str
    stage: Literal["accepted", "dispatched", "exec_confirmed", "completed", "unknown"]
    outcome: Literal["succeeded", "failed", "cancelled", "timed_out", "unknown"] | None

    @field_validator("mount_id")
    @classmethod
    def _valid_mount_id(cls, value: str) -> str:
        return _require_identifier(value, "mount_id")


class EnvironmentPath(BaseModel):
    model_config = ConfigDict(frozen=True)

    mount_id: str
    path: str

    @field_validator("mount_id")
    @classmethod
    def _valid_mount_id(cls, value: str) -> str:
        return _require_identifier(value, "mount_id")


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
