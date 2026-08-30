"""Immutable provider-neutral Environment values and failures."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator, model_validator

from a13n_harness.errors import HarnessError

type EnvironmentOperationFamily = Literal["files", "shell", "processes", "ports", "outputs", "state"]
ENVIRONMENT_OPERATION_FAMILIES = frozenset({"files", "shell", "processes", "ports", "outputs", "state"})
ENVIRONMENT_ACTION_CATALOG_VERSION = "environment-actions/2"
DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS = 600.0
DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS = 600.0

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class EnvironmentError(HarnessError):
    """Environment binding, operation, or lifecycle failed."""


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


def _require_alias(value: str) -> str:
    if not _ALIAS_PATTERN.fullmatch(value):
        raise ValueError("alias must match ^[a-z][a-z0-9-]{0,62}$")
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


class EnvironmentTopologyLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_bindings: Annotated[int, Field(gt=0, le=1024)] = 32
    max_committed_changes: Annotated[int, Field(gt=0, le=100_000)] = 1024


class EnvironmentStateLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_binding_entries: Annotated[int, Field(gt=0, le=1024)] = 32
    max_binding_encoded_bytes: Annotated[int, Field(gt=0)] = 1_048_576
    max_aggregate_encoded_bytes: Annotated[int, Field(gt=0)] = 4_194_304
    export_timeout_seconds: float = 600.0
    restore_timeout_seconds: float = 600.0

    @model_validator(mode="after")
    def _validate_timeouts(self) -> EnvironmentStateLimits:
        _positive_finite(self.export_timeout_seconds, "export_timeout_seconds")
        _positive_finite(self.restore_timeout_seconds, "restore_timeout_seconds")
        return self


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


@dataclass(frozen=True, slots=True)
class EnvironmentBindingRequest:
    binding_id: str
    binding_version: int
    alias: str
    permission_ceiling: EnvironmentPermissionSet
    default_working_directory: str | None
    provider_binding: Any | None

    def __post_init__(self) -> None:
        _require_identifier(self.binding_id, "binding_id")
        if self.binding_version <= 0:
            raise ValueError("binding_version must be positive")
        _require_alias(self.alias)
        if self.default_working_directory is not None and "\x00" in self.default_working_directory:
            raise ValueError("default_working_directory contains NUL")


@dataclass(frozen=True, slots=True)
class EnvironmentTopologyRequest:
    topology_version: int
    bindings: tuple[EnvironmentBindingRequest, ...]
    default_binding_id: str | None

    def __post_init__(self) -> None:
        if self.topology_version < 0:
            raise ValueError("topology_version must be non-negative")
        object.__setattr__(self, "bindings", tuple(self.bindings))
        if self.default_binding_id is not None:
            _require_identifier(self.default_binding_id, "default_binding_id")


class EnvironmentBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str
    binding_version: Annotated[int, Field(gt=0)]
    alias: str
    provider_type: str
    descriptor: EnvironmentDescriptor
    permission_ceiling: EnvironmentPermissionSet
    default_working_directory: str | None

    @field_validator("binding_id", "provider_type")
    @classmethod
    def _valid_id(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @field_validator("alias")
    @classmethod
    def _valid_alias(cls, value: str) -> str:
        return _require_alias(value)


class EnvironmentBindingObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding: EnvironmentBinding
    availability: EnvironmentAvailability


class EnvironmentTopology(BaseModel):
    model_config = ConfigDict(frozen=True)

    topology_version: Annotated[int, Field(ge=0)]
    bindings: tuple[EnvironmentBinding, ...]
    default_binding_id: str | None


class EnvironmentReadinessRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: frozenset[EnvironmentOperationFamily]
    binding_ids: frozenset[str] | None = None
    timeout_seconds: float | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> EnvironmentReadinessRequirement:
        if not self.operations:
            raise ValueError("operations must not be empty")
        if self.binding_ids is not None and not self.binding_ids:
            raise ValueError("binding_ids must not be empty")
        if self.timeout_seconds is not None:
            _positive_finite(self.timeout_seconds, "timeout_seconds")
        return self


class EnvironmentBindingState(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_type: str
    state_version: str
    resource_compatibility: Literal["same_logical_resource", "portable"]
    observed_generation: str | None = None
    data: JsonValue

    @field_validator("provider_type", "state_version")
    @classmethod
    def _valid_state_id(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)


class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    observed_topology_version: Annotated[int, Field(ge=0)]
    bindings: Mapping[str, EnvironmentBindingState] = Field(default_factory=dict)

    @field_validator("bindings", mode="after")
    @classmethod
    def _immutable_bindings(
        cls,
        value: Mapping[str, EnvironmentBindingState],
    ) -> Mapping[str, EnvironmentBindingState]:
        return MappingProxyType({_require_identifier(key, "binding_id"): item for key, item in value.items()})

    @field_serializer("bindings")
    def _serialize_bindings(
        self,
        value: Mapping[str, EnvironmentBindingState],
    ) -> dict[str, EnvironmentBindingState]:
        return dict(value)


class EnvironmentOperationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str
    binding_version: Annotated[int, Field(gt=0)]
    observed_generation: str
    operation_id: str
    stage: Literal["accepted", "dispatched", "exec_confirmed", "completed", "unknown"]
    outcome: Literal["succeeded", "failed", "cancelled", "timed_out", "unknown"] | None


class EnvironmentPath(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str
    binding_version: Annotated[int, Field(gt=0)]
    path: str

    @field_validator("binding_id")
    @classmethod
    def _valid_binding_id(cls, value: str) -> str:
        return _require_identifier(value, "binding_id")


class EnvironmentTopologyBindingChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["added", "removed", "refreshed"]
    binding_id: str
    previous_version: int | None
    current_version: int | None
    previous_alias: str | None
    current_alias: str | None


class EnvironmentTopologyChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous_version: Annotated[int, Field(ge=0)]
    current_version: Annotated[int, Field(ge=0)]
    request_digest: str
    bindings: tuple[EnvironmentTopologyBindingChange, ...]
