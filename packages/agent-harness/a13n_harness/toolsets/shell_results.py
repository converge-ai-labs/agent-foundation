"""Typed model results returned by the compact ShellToolset."""

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
    process_id: str
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
    background: bool
    status: ProcessStatusProjection
    stdout: OutputCaptureProjection
    stderr: OutputCaptureProjection
    process_id: NotRequired[str]
    stdin_open: NotRequired[bool]
    disclosure: NotRequired[ToolOutputDisclosure]


type ShellExecToolResult = ShellExecSuccess | ToolFailure


class ProcessProducedBytes(TypedDict):
    stdout: int
    stderr: int


class ProcessStatusItemSuccess(TypedDict):
    process_id: str
    ok: Literal[True]
    status: ProcessStatusProjection
    stdin_open: bool
    produced_bytes: ProcessProducedBytes


class ProcessStatusItemFailure(TypedDict):
    process_id: str
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


class ProcessReadOutputSuccess(ProcessSuccess):
    pass


type ProcessReadOutputResult = ProcessReadOutputSuccess | ToolFailure


class ProcessInputSuccess(TypedDict):
    ok: Literal[True]
    accepted_bytes: int
    stdin_open: bool


type ProcessInputResult = ProcessInputSuccess | ToolFailure


class ProcessSignalSuccess(ProcessProjection):
    ok: Literal[True]
    accepted: bool


type ProcessSignalResult = ProcessSignalSuccess | ToolFailure


__all__ = [
    "OutputCaptureProjection",
    "ProcessInputResult",
    "ProcessProjection",
    "ProcessReadOutputResult",
    "ProcessSignalResult",
    "ProcessStatusItem",
    "ProcessStatusListResult",
    "ProcessStatusProjection",
    "ProcessToolResult",
    "ShellExecToolResult",
]
