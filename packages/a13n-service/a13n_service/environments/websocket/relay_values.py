"""Wire values for binary data and Session-local opaque provider tokens.

The EIP adapter owns the native handle registries. These values carry its opaque
tokens, not a second registry or a portable reference across Attempt uses.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.environment.commands import (
    BoundProcessHandle,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    CommandSpec,
    ProcessControlResult,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStartResult,
    ProcessStatus,
    ProcessStreamRead,
    ShellExecResult,
)
from a13n_harness.providers.environment.models import EnvironmentOperationReceipt
from a13n_harness.providers.environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    EnvironmentOutputSegment,
    OpaqueOutputCursor,
    OpaqueOutputReference,
    OpaqueProcessHandle,
    _unwrap_opaque,
)
from pydantic import Field, StringConstraints, field_validator, model_validator

from ..domain import DomainModel

type OpaqueToken = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class BinaryValue(DomainModel):
    base64: str = Field(max_length=262_144, repr=False)

    @field_validator("base64")
    @classmethod
    def _canonical(cls, value: str) -> str:
        decoded = base64.b64decode(value, validate=True)
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("Relay bytes must use canonical padded base64")
        return value

    @classmethod
    def from_value(cls, value: bytes) -> BinaryValue:
        return cls(base64=base64.b64encode(value).decode("ascii"))

    def to_value(self) -> bytes:
        return base64.b64decode(self.base64, validate=True)


class ProcessHandle(DomainModel):
    mount_id: str
    identity: ProcessIdentity = Field(repr=False)
    observed_generation: str
    token: OpaqueToken = Field(repr=False)

    @model_validator(mode="after")
    def _native_binding(self) -> ProcessHandle:
        self.to_value()
        return self

    @classmethod
    def from_value(cls, value: BoundProcessHandle) -> ProcessHandle:
        return cls(
            mount_id=value.mount_id,
            identity=value.identity,
            observed_generation=value.observed_generation,
            token=_unwrap_opaque(value.handle, OpaqueProcessHandle),
        )

    def to_value(self) -> BoundProcessHandle:
        return BoundProcessHandle(
            mount_id=self.mount_id,
            identity=self.identity,
            observed_generation=self.observed_generation,
            handle=OpaqueProcessHandle._from_payload(self.token),
        )


class OutputReference(DomainModel):
    mount_id: str
    observed_generation: str
    token: OpaqueToken = Field(repr=False)

    @classmethod
    def from_value(cls, value: BoundOutputReference) -> OutputReference:
        return cls(
            mount_id=value.mount_id,
            observed_generation=value.observed_generation,
            token=_unwrap_opaque(value.reference, OpaqueOutputReference),
        )

    def to_value(self) -> BoundOutputReference:
        return BoundOutputReference(
            mount_id=self.mount_id,
            observed_generation=self.observed_generation,
            reference=OpaqueOutputReference._from_payload(self.token),
        )


class OutputCursor(DomainModel):
    mount_id: str
    observed_generation: str
    token: OpaqueToken = Field(repr=False)

    @classmethod
    def from_value(cls, value: BoundOutputCursor) -> OutputCursor:
        return cls(
            mount_id=value.mount_id,
            observed_generation=value.observed_generation,
            token=_unwrap_opaque(value.cursor, OpaqueOutputCursor),
        )

    def to_value(self) -> BoundOutputCursor:
        return BoundOutputCursor(
            mount_id=self.mount_id,
            observed_generation=self.observed_generation,
            cursor=OpaqueOutputCursor._from_payload(self.token),
        )


class OutputSegment(DomainModel):
    start_offset: int = Field(ge=0)
    data: BinaryValue = Field(repr=False)

    @classmethod
    def from_value(cls, value: EnvironmentOutputSegment) -> OutputSegment:
        return cls(start_offset=value.start_offset, data=BinaryValue.from_value(value.data))

    def to_value(self) -> EnvironmentOutputSegment:
        return EnvironmentOutputSegment(start_offset=self.start_offset, data=self.data.to_value())


class OutputCapture(DomainModel):
    kind: Literal["empty", "inline", "retained", "truncated"]
    origin: Literal["native_bytes", "sdk_text"]
    coverage: Literal["complete", "partial", "unknown"]
    observation_closed: bool
    reason: Literal["reattached", "connection_lost", "observation_limit", "observation_evicted"] | None
    producer_complete: bool | None
    content_complete: bool
    produced_bytes: int | None = Field(ge=0)
    captured_bytes: int = Field(ge=0)
    dropped_bytes: int | None = Field(ge=0)
    inline: BinaryValue | None = Field(repr=False)
    preview: tuple[OutputSegment, ...] = Field(repr=False)
    reference: OutputReference | None
    cursor: OutputCursor | None
    available_start: int = Field(ge=0)
    available_end: int = Field(ge=0)
    expires_at: datetime | None

    @classmethod
    def from_value(cls, value: EnvironmentOutputCapture) -> OutputCapture:
        return cls(
            **value.model_dump(exclude={"inline", "preview", "reference", "cursor"}),
            inline=None if value.inline is None else BinaryValue.from_value(value.inline),
            preview=tuple(OutputSegment.from_value(segment) for segment in value.preview),
            reference=None if value.reference is None else OutputReference.from_value(value.reference),
            cursor=None if value.cursor is None else OutputCursor.from_value(value.cursor),
        )

    def to_value(self) -> EnvironmentOutputCapture:
        return EnvironmentOutputCapture(
            **self.model_dump(exclude={"inline", "preview", "reference", "cursor"}),
            inline=None if self.inline is None else self.inline.to_value(),
            preview=tuple(segment.to_value() for segment in self.preview),
            reference=None if self.reference is None else self.reference.to_value(),
            cursor=None if self.cursor is None else self.cursor.to_value(),
        )


class OutputSnapshot(DomainModel):
    stdout: OutputCapture
    stderr: OutputCapture

    @classmethod
    def from_value(cls, value: ProcessOutputSnapshot) -> OutputSnapshot:
        return cls(stdout=OutputCapture.from_value(value.stdout), stderr=OutputCapture.from_value(value.stderr))

    def to_value(self) -> ProcessOutputSnapshot:
        return ProcessOutputSnapshot(stdout=self.stdout.to_value(), stderr=self.stderr.to_value())


class ProcessObservation(DomainModel):
    handle: ProcessHandle
    status: ProcessStatus
    stdin_open: bool | None
    output: OutputSnapshot | None

    @classmethod
    def from_value(cls, value: ProcessInfo) -> ProcessObservation:
        return cls(
            handle=ProcessHandle.from_value(value.handle),
            status=value.status,
            stdin_open=value.stdin_open,
            output=None if value.output is None else OutputSnapshot.from_value(value.output),
        )

    def to_value(self) -> ProcessInfo:
        return ProcessInfo(
            handle=self.handle.to_value(),
            status=self.status,
            stdin_open=self.stdin_open,
            output=None if self.output is None else self.output.to_value(),
        )


class OutputPage(DomainModel):
    chunks: tuple[OutputSegment, ...] = Field(repr=False)
    next_cursor: OutputCursor | None
    capture: OutputCapture

    @classmethod
    def from_value(cls, value: ProcessStreamRead | EnvironmentOutputReadResult) -> OutputPage:
        return cls(
            chunks=tuple(OutputSegment.from_value(segment) for segment in value.chunks),
            next_cursor=None if value.next_cursor is None else OutputCursor.from_value(value.next_cursor),
            capture=OutputCapture.from_value(value.capture),
        )

    def to_value(self) -> ProcessStreamRead:
        return ProcessStreamRead(
            chunks=tuple(segment.to_value() for segment in self.chunks),
            next_cursor=None if self.next_cursor is None else self.next_cursor.to_value(),
            capture=self.capture.to_value(),
        )

    def to_output_value(self) -> EnvironmentOutputReadResult:
        page = self.to_value()
        return EnvironmentOutputReadResult(chunks=page.chunks, next_cursor=page.next_cursor, capture=page.capture)


class CommandInput(DomainModel):
    command: CommandSpec = Field(repr=False)
    cwd: str | None
    environment: CommandEnvironment = Field(repr=False)
    network: Literal["configured", "deny"]
    limits: CommandLimits
    initial_stdin: BinaryValue | None = Field(repr=False)
    keep_stdin_open: bool
    output_policy: EnvironmentOutputPolicy

    @model_validator(mode="after")
    def _native_request(self) -> CommandInput:
        self.to_value()
        return self

    @classmethod
    def from_value(cls, value: CommandRequest) -> CommandInput:
        return cls(
            **value.model_dump(exclude={"initial_stdin"}),
            initial_stdin=None if value.initial_stdin is None else BinaryValue.from_value(value.initial_stdin),
        )

    def to_value(self) -> CommandRequest:
        return CommandRequest(
            **self.model_dump(exclude={"initial_stdin"}),
            initial_stdin=None if self.initial_stdin is None else self.initial_stdin.to_value(),
        )


class ProcessReceipt(DomainModel):
    process: ProcessObservation
    receipt: EnvironmentOperationReceipt

    @classmethod
    def from_value(cls, value: ProcessStartResult | ProcessControlResult) -> ProcessReceipt:
        return cls(process=ProcessObservation.from_value(value.process), receipt=value.receipt)

    def to_start(self) -> ProcessStartResult:
        return ProcessStartResult(process=self.process.to_value(), receipt=self.receipt)

    def to_control(self) -> ProcessControlResult:
        return ProcessControlResult(process=self.process.to_value(), receipt=self.receipt)


class SignalReceipt(DomainModel):
    accepted: bool
    process: ProcessObservation
    receipt: EnvironmentOperationReceipt

    @classmethod
    def from_value(cls, value: ProcessSignalResult) -> SignalReceipt:
        return cls(accepted=value.accepted, process=ProcessObservation.from_value(value.process), receipt=value.receipt)

    def to_value(self) -> ProcessSignalResult:
        return ProcessSignalResult(accepted=self.accepted, process=self.process.to_value(), receipt=self.receipt)


class ShellResult(DomainModel):
    status: ProcessStatus
    output: OutputSnapshot
    receipt: EnvironmentOperationReceipt

    @classmethod
    def from_value(cls, value: ShellExecResult) -> ShellResult:
        return cls(status=value.status, output=OutputSnapshot.from_value(value.output), receipt=value.receipt)

    def to_value(self) -> ShellExecResult:
        return ShellExecResult(status=self.status, output=self.output.to_value(), receipt=self.receipt)


class ProcessOutput(DomainModel):
    process: ProcessObservation
    stdout: OutputPage
    stderr: OutputPage

    @classmethod
    def from_value(cls, value: ProcessReadOutputResult) -> ProcessOutput:
        return cls(
            process=ProcessObservation.from_value(value.process),
            stdout=OutputPage.from_value(value.stdout),
            stderr=OutputPage.from_value(value.stderr),
        )

    def to_value(self) -> ProcessReadOutputResult:
        return ProcessReadOutputResult(
            process=self.process.to_value(), stdout=self.stdout.to_value(), stderr=self.stderr.to_value()
        )
