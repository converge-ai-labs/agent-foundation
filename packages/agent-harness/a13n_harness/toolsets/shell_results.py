"""Typed model results returned by the compact ShellToolset."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from ._results import ToolFailure
from .output import ToolOutputDisclosure


class ProcessStatusProjection(TypedDict):
    phase: str
    termination_reason: str | None
    exit_code: int | None
    signal: str | None
    cleanup: str


class OutputPageProjection(TypedDict):
    requested_offset: int
    start_offset: int
    next_offset: int
    available_start: int
    available_end: int
    produced_bytes: int
    producer_complete: bool
    content_complete: bool
    omitted_before_bytes: int
    text: str


class ProcessObservationSuccess(TypedDict):
    ok: Literal[True]
    process_id: str
    status: ProcessStatusProjection
    stdin_open: bool
    stdout: OutputPageProjection
    stderr: OutputPageProjection
    disclosure: NotRequired[ToolOutputDisclosure]


type ProcessObservationResult = ProcessObservationSuccess | ToolFailure


class ShellExecSuccess(TypedDict):
    ok: Literal[True]
    process_id: NotRequired[str]
    status: ProcessStatusProjection
    stdin_open: bool
    stdout: OutputPageProjection
    stderr: OutputPageProjection
    disclosure: NotRequired[ToolOutputDisclosure]


type ShellExecToolResult = ShellExecSuccess | ToolFailure


class ProcessInputSuccess(TypedDict):
    ok: Literal[True]
    process_id: str
    accepted_bytes: int
    stdin_open: bool
    status: ProcessStatusProjection


type ProcessInputResult = ProcessInputSuccess | ToolFailure


class ProcessSignalSuccess(TypedDict):
    ok: Literal[True]
    process_id: str
    accepted: bool
    stdin_open: bool
    status: ProcessStatusProjection


type ProcessSignalResult = ProcessSignalSuccess | ToolFailure


__all__ = [
    "OutputPageProjection",
    "ProcessInputResult",
    "ProcessObservationResult",
    "ProcessSignalResult",
    "ProcessStatusProjection",
    "ShellExecToolResult",
]
