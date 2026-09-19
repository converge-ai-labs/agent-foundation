"""Provider-neutral bounded file values and semantic protocol."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .models import EnvironmentOperationReceipt

type FileWriteMode = Literal["create", "replace", "upsert", "append"]
type FileKind = Literal["file", "directory", "symlink", "other"]
type FileIgnoreMode = Literal["none", "git"]


class FileCommitCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class FileCommitWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    text: str


class FileCommitRequest(BaseModel):
    """Conditional ordered publication, with recoverable partial completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    root: str
    conditions: tuple[FileCommitCondition, ...] = Field(default=(), max_length=256)
    directories: tuple[str, ...] = Field(default=(), max_length=256)
    writes: tuple[FileCommitWrite, ...] = Field(default=(), max_length=256)
    removals: tuple[str, ...] = Field(default=(), max_length=256)


@runtime_checkable
class FileCommitOperator(Protocol):
    """Optional native capability; ordinary file writes are not a fallback."""

    async def commit(self, request: FileCommitRequest) -> FileMutationResult: ...


class FileTextResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    text: str
    line_offset: int = Field(ge=0)
    lines_read: int = Field(ge=0)
    has_more: bool
    truncated_lines: tuple[int, ...] = ()


class FileWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes_written: int = Field(ge=0)
    receipt: EnvironmentOperationReceipt


class FilePatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    hunks_applied: int = Field(ge=0)
    receipt: EnvironmentOperationReceipt


class FileCopyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes_copied: int = Field(ge=0)
    receipt: EnvironmentOperationReceipt


class FileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    kind: FileKind
    size: int | None = Field(default=None, ge=0)
    writable: bool


class FileEntriesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: tuple[FileMetadata, ...]
    offset: int = Field(ge=0)
    has_more: bool


class FileQueryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    pattern: str
    recursive: bool = True
    include_hidden: bool = False
    ignore_mode: FileIgnoreMode = "none"
    kinds: frozenset[FileKind] | None = None
    offset: int = Field(default=0, ge=0)
    max_results: int = Field(gt=0)


class FileTextMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    line: int = Field(ge=1)
    text: str
    text_truncated: bool = False
    context: str = ""
    context_start_line: int = Field(default=1, ge=1)


class FileTextSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str = Field(description="Provider-local regular file or directory; explicit files bypass discovery filters")
    pattern: str
    regex: bool = False
    case_sensitive: bool = True
    include: str = "**/*"
    include_hidden: bool = False
    ignore_mode: FileIgnoreMode = "none"
    context_lines: int = Field(default=0, ge=0, le=20)
    offset: int = Field(default=0, ge=0)
    max_matches: int = Field(gt=0)
    max_matches_per_file: int | None = Field(default=None, gt=0)
    max_files: int | None = Field(default=None, gt=0)
    max_file_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_line_length: int = Field(default=2_000, gt=0)


class FileTextSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    matches: tuple[FileTextMatch, ...]
    offset: int = Field(ge=0)
    has_more: bool


class FileMutationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    receipt: EnvironmentOperationReceipt


class FileOperator(Protocol):
    async def read_text(
        self,
        path: str,
        *,
        line_offset: int = 0,
        line_limit: int = 200,
        max_line_length: int = 2_000,
    ) -> FileTextResult: ...

    async def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes: ...

    def read_bytes_stream(
        self,
        path: str,
        *,
        chunk_size: int = 65_536,
    ) -> AsyncIterator[bytes]: ...

    async def write_bytes_stream(
        self,
        path: str,
        stream: AsyncIterable[bytes],
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult: ...

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult: ...

    async def patch_text(
        self,
        path: str,
        patch: str,
    ) -> FilePatchResult: ...

    async def stat(self, path: str) -> FileMetadata: ...

    async def list(
        self,
        path: str,
        *,
        offset: int = 0,
        max_results: int,
        include_hidden: bool = False,
    ) -> FileEntriesResult: ...

    async def query(self, request: FileQueryRequest) -> FileEntriesResult: ...

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult: ...

    async def mkdir(
        self,
        path: str,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> FileMutationResult: ...

    async def move(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileMutationResult: ...

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> FileMutationResult: ...

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileCopyResult: ...
