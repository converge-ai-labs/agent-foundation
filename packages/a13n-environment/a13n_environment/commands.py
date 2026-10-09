"""Provider-neutral command, process, and port operation values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .models import EnvironmentOperationReceipt
from .retention import (
    BoundOutputCursor,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputSegment,
    OpaqueProcessHandle,
)


class ArgvCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["argv"] = "argv"
    executable: str
    arguments: tuple[str, ...] = ()


class ShellCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["shell"] = "shell"
    profile_id: str
    script: str
    login: bool | None = None


type CommandSpec = Annotated[ArgvCommand | ShellCommand, Field(discriminator="kind")]


class CommandEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    set: Mapping[str, str] = Field(default_factory=dict)
    unset: tuple[str, ...] = ()

    @field_validator("set", mode="after")
    @classmethod
    def _detach_set(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("set")
    def _serialize_set(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_keys(self) -> CommandEnvironment:
        keys = tuple(self.set) + self.unset
        if len(keys) != len(set(keys)):
            raise ValueError("command environment keys must be unique across set and unset")
        if any(not key or "\x00" in key or "=" in key for key in keys):
            raise ValueError("command environment keys are invalid")
        if any("\x00" in value for value in self.set.values()):
            raise ValueError("command environment values cannot contain NUL")
        return self


class CommandLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    wall_time_seconds: float | None = None
    stdin_bytes: int | None = Field(default=None, gt=0)
    process_count: int | None = Field(default=None, gt=0)
    memory_bytes: int | None = Field(default=None, gt=0)
    cpu_time_seconds: float | None = None

    @model_validator(mode="after")
    def _finite_times(self) -> CommandLimits:
        for name in ("wall_time_seconds", "cpu_time_seconds"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite")
        return self


class CommandRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: CommandSpec
    cwd: str | None = None
    environment: CommandEnvironment = CommandEnvironment()
    network: Literal["configured", "deny"] = "configured"
    limits: CommandLimits = CommandLimits()
    initial_stdin: bytes | None = None
    keep_stdin_open: bool = False
    output_policy: EnvironmentOutputPolicy

    @model_validator(mode="after")
    def _bounded_shape(self) -> CommandRequest:
        if self.cwd is not None and (not self.cwd or "\x00" in self.cwd):
            raise ValueError("cwd is invalid")
        command = self.command
        values = (
            (command.executable, *command.arguments)
            if isinstance(command, ArgvCommand)
            else (command.profile_id, command.script)
        )
        if any(not value or "\x00" in value for value in values):
            raise ValueError("command values must be non-empty and cannot contain NUL")
        if len(values) > 1025 or sum(len(value.encode()) for value in values) > 1_048_576:
            raise ValueError("command shape is too large")
        if len(self.environment.set) + len(self.environment.unset) > 1024:
            raise ValueError("command environment has too many entries")
        return self


type ProcessPhase = Literal[
    "starting", "running", "exited", "signaled", "timed_out", "cancelled", "failed", "unknown", "missing"
]
type ProcessCleanupOutcome = Literal["pending", "complete", "residual_confined", "failed"]
type ProcessTerminationReason = Literal["exit", "signal", "timeout", "cancelled", "output_limit", "backend_lost"]
type ProcessSignal = Literal["interrupt", "terminate", "kill"]


class ProcessStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    phase: ProcessPhase
    termination_reason: ProcessTerminationReason | None = None
    exit_code: int | None = None
    signal: ProcessSignal | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cleanup: ProcessCleanupOutcome | None = None


class ProcessOutputSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    stdout: EnvironmentOutputCapture
    stderr: EnvironmentOutputCapture


class ProcessIdentity(BaseModel):
    """Portable identity of one provider-owned process in one Environment instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_type: str = Field(min_length=1, max_length=256)
    environment_id: str = Field(min_length=1, max_length=256)
    generation: str = Field(min_length=1, max_length=256)
    process_id: str = Field(min_length=1, max_length=1024)


class BoundProcessHandle(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution_id: str
    identity: ProcessIdentity
    handle: OpaqueProcessHandle
    observed_generation: str

    @model_validator(mode="after")
    def _identity_matches_binding_generation(self) -> BoundProcessHandle:
        if self.identity.generation != self.observed_generation:
            raise ValueError("process identity generation must match the bound generation")
        return self


class ProcessInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    handle: BoundProcessHandle
    status: ProcessStatus
    stdin_open: bool | None = None
    output: ProcessOutputSnapshot | None = None


class ProcessDiscovery(BaseModel):
    """Bounded native process observations; listing never attaches output streams."""

    model_config = ConfigDict(frozen=True)

    processes: tuple[ProcessInfo, ...]
    has_more: bool = False


class ShellExecResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ProcessStatus
    output: ProcessOutputSnapshot
    receipt: EnvironmentOperationReceipt


class ProcessStartResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    process: ProcessInfo
    receipt: EnvironmentOperationReceipt


class ProcessControlResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    process: ProcessInfo
    receipt: EnvironmentOperationReceipt


class ProcessStreamRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunks: tuple[EnvironmentOutputSegment, ...]
    next_cursor: BoundOutputCursor | None
    capture: EnvironmentOutputCapture


class ProcessReadOutputResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    process: ProcessInfo
    stdout: ProcessStreamRead
    stderr: ProcessStreamRead


class ProcessWriteStdinResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted_bytes: int = Field(ge=0)
    stdin_open: bool | None
    receipt: EnvironmentOperationReceipt


class ProcessSignalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    process: ProcessInfo
    receipt: EnvironmentOperationReceipt


class PortTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    address: Literal["loopback", "any"] = "loopback"
    port: int = Field(ge=1, le=65535)


class PortObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: PortTarget
    status: Literal["listening", "not_listening", "unknown"]
    observed_at: datetime


class ProviderShellOperations(Protocol):
    async def exec(self, request: CommandRequest) -> ShellExecResult: ...


class ProviderProcessOperations(Protocol):
    async def list(self, *, limit: int) -> ProcessDiscovery: ...

    async def start(self, request: CommandRequest) -> ProcessStartResult: ...

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo: ...

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo: ...

    async def read_output(
        self,
        handle: BoundProcessHandle,
        *,
        stdout_cursor: BoundOutputCursor | None = None,
        stderr_cursor: BoundOutputCursor | None = None,
        stdout_start_offset: int | None = None,
        stderr_start_offset: int | None = None,
        wait_seconds: float = 0,
        policy: EnvironmentOutputPolicy,
    ) -> ProcessReadOutputResult: ...

    async def write_stdin(
        self,
        handle: BoundProcessHandle,
        data: bytes,
        *,
        close_after_write: bool = False,
    ) -> ProcessWriteStdinResult: ...

    async def close_stdin(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt: ...

    async def signal(
        self, handle: BoundProcessHandle, signal: Literal["interrupt", "terminate"]
    ) -> ProcessSignalResult: ...

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: Literal["initial_terminal", "tree_cleaned"],
        timeout_seconds: float,
    ) -> ProcessInfo: ...

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult: ...

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt: ...


class ProviderPortOperations(Protocol):
    async def inspect(self, target: PortTarget) -> PortObservation: ...

    async def wait(
        self,
        target: PortTarget,
        *,
        desired: Literal["listening", "not_listening"],
        timeout_seconds: float,
    ) -> PortObservation: ...
