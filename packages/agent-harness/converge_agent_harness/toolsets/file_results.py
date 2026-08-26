"""Typed model results returned by FileToolset."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic_ai import ToolReturn

from ._results import ToolError, ToolFailure
from .output import ToolOutputDisclosure


class FileMetadataProjection(TypedDict):
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None
    writable: bool


class FileViewSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    content: str
    line_offset: int
    lines_read: int
    has_more: bool
    next_line_offset: NotRequired[int]
    truncated_lines: list[int]
    disclosure: NotRequired[ToolOutputDisclosure]


type FileViewResult = FileViewSuccess | ToolFailure | ToolReturn


class FileWriteSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    bytes_written: int


type FileWriteResult = FileWriteSuccess | ToolFailure


class FileEditSuccess(TypedDict):
    ok: Literal[True]
    file_path: str
    edits_applied: int
    bytes_written: int
    created: bool


type FileEditResult = FileEditSuccess | ToolFailure


class FileMutationItem(TypedDict):
    ok: bool
    path: str
    error: NotRequired[ToolError]


class FileMkdirResult(TypedDict):
    ok: bool
    results: list[FileMutationItem]
    count: int


class FilePathPairItem(TypedDict):
    ok: bool
    src: str
    dst: str
    error: NotRequired[ToolError]


class FileMoveResult(TypedDict):
    ok: bool
    results: list[FilePathPairItem]
    count: int


class FileCopyItem(FilePathPairItem):
    bytes_copied: NotRequired[int]


class FileCopyToolResult(TypedDict):
    ok: bool
    results: list[FileCopyItem]
    count: int


class FileDeleteResult(TypedDict):
    ok: bool
    results: list[FileMutationItem]
    count: int


class FileListSuccess(TypedDict):
    ok: Literal[True]
    path: str
    entries: list[FileMetadataProjection]
    count: int
    showing: int
    has_more: bool
    next_offset: int | None
    disclosure: NotRequired[ToolOutputDisclosure]


type FileListResult = FileListSuccess | ToolFailure


class FileGlobSuccess(TypedDict):
    ok: Literal[True]
    files: list[str]
    count: int
    showing: int
    has_more: bool
    next_offset: int | None
    disclosure: NotRequired[ToolOutputDisclosure]


type FileGlobResult = FileGlobSuccess | ToolFailure


class GrepMatchProjection(TypedDict):
    file_path: str
    line_number: int
    matching_line: str
    text_truncated: bool
    context: str
    context_start_line: int


class FileGrepSuccess(TypedDict):
    ok: Literal[True]
    matches: dict[str, GrepMatchProjection]
    count: int
    showing: int
    has_more: bool
    next_offset: int | None
    disclosure: NotRequired[ToolOutputDisclosure]


type FileGrepResult = FileGrepSuccess | ToolFailure


__all__ = [
    "FileCopyItem",
    "FileCopyToolResult",
    "FileDeleteResult",
    "FileEditResult",
    "FileGlobResult",
    "FileGrepResult",
    "FileListResult",
    "FileMetadataProjection",
    "FileMkdirResult",
    "FileMoveResult",
    "FileMutationItem",
    "FilePathPairItem",
    "FileViewResult",
    "FileWriteResult",
]
