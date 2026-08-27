"""Typed model results returned by ShellToolset and process extensions."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import JsonValue

from ._results import ToolFailure
from .output import ToolOutputDisclosure


class ProcessStatusProjection(TypedDict):
    phase: str
    termination_reason: str | None
    exit_code: int | None
    signal: str | None
    cleanup: str


class OutputCaptureProjection(TypedDict):
    kind: str
    producer_complete: bool
    content_complete: bool
    produced_bytes: int
    captured_bytes: int
    dropped_bytes: int
    text: str
    available_start: int
    available_end: int


class ProcessProjection(TypedDict):
    process: str
    status: ProcessStatusProjection
    stdin_open: bool
    stdout: OutputCaptureProjection
    stderr: OutputCaptureProjection
    disclosure: NotRequired[ToolOutputDisclosure]


class ProcessSuccess(ProcessProjection):
    ok: Literal[True]


type ProcessToolResult = ProcessSuccess | ToolFailure


class ShellExecSuccess(TypedDict):
    ok: Literal[True]
    status: ProcessStatusProjection
    stdout: OutputCaptureProjection
    stderr: OutputCaptureProjection
    disclosure: NotRequired[ToolOutputDisclosure]


type ShellExecToolResult = ShellExecSuccess | ToolFailure


class ProcessProducedBytes(TypedDict):
    stdout: int
    stderr: int


class ProcessStatusItemSuccess(TypedDict):
    process: str
    ok: Literal[True]
    status: ProcessStatusProjection
    stdin_open: bool
    produced_bytes: ProcessProducedBytes


class ProcessStatusItemFailure(TypedDict):
    process: str
    ok: Literal[False]
    error: dict[str, JsonValue]


type ProcessStatusItem = ProcessStatusItemSuccess | ProcessStatusItemFailure


class ProcessStatusListSuccess(TypedDict):
    ok: Literal[True]
    processes: list[ProcessStatusItem]
    showing: int
    next_cursor: int | None
    truncated: bool
    disclosure: NotRequired[ToolOutputDisclosure]


type ProcessStatusListResult = ProcessStatusListSuccess | ToolFailure


class ProcessReadOutputSuccess(TypedDict):
    ok: Literal[True]
    process: ProcessProjection
    stdout: OutputCaptureProjection
    stderr: OutputCaptureProjection
    disclosure: NotRequired[ToolOutputDisclosure]


type ProcessReadOutputResult = ProcessReadOutputSuccess | ToolFailure


class ProcessWriteStdinSuccess(TypedDict):
    ok: Literal[True]
    accepted_bytes: int
    stdin_open: bool


class BooleanSuccess(TypedDict):
    ok: Literal[True]
    closed: bool


class ProcessSignalSuccess(TypedDict):
    ok: Literal[True]
    accepted: bool
    process: ProcessProjection


class ReleaseSuccess(TypedDict):
    ok: Literal[True]
    released: Literal[True]


type ProcessWriteStdinResult = ProcessWriteStdinSuccess | ToolFailure
type ProcessCloseStdinResult = BooleanSuccess | ToolFailure
type ProcessSignalResult = ProcessSignalSuccess | ToolFailure
type ReleaseResult = ReleaseSuccess | ToolFailure


class PortProjection(TypedDict):
    port: int
    address: str
    status: str
    observed_at: str


class PortSuccess(PortProjection):
    ok: Literal[True]


type PortToolResult = PortSuccess | ToolFailure


__all__ = [
    "OutputCaptureProjection",
    "PortProjection",
    "PortToolResult",
    "ProcessCloseStdinResult",
    "ProcessProjection",
    "ProcessReadOutputResult",
    "ProcessSignalResult",
    "ProcessStatusItem",
    "ProcessStatusListResult",
    "ProcessStatusProjection",
    "ProcessToolResult",
    "ProcessWriteStdinResult",
    "ReleaseResult",
    "ShellExecToolResult",
]
