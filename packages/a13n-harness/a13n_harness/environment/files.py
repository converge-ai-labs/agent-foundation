"""Provider-neutral bounded file values and semantic protocol."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.models import EnvironmentError, EnvironmentOperationReceipt

type FileWriteMode = Literal["create", "replace", "upsert", "append"]
MAX_FILE_BATCH_OPERATIONS = 128
MAX_FILE_BATCH_BYTES = 64 * 1024
type FileKind = Literal["file", "directory", "symlink", "other"]
type FileIgnoreMode = Literal["none", "git"]


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


class FileDocumentRequest(BaseModel):
    """Observe a named document at a root and its immediate nonhidden directories."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: str
    basename: str = Field(min_length=1)
    max_entries: int = Field(default=256, gt=0, le=10_000)
    line_limit: int = Field(default=256, gt=0)
    max_line_length: int = Field(default=16 * 1024, gt=0)
    max_bytes: int | None = Field(default=None, gt=0)

    @field_validator("basename")
    @classmethod
    def _basename(cls, value: str) -> str:
        if value in {".", ".."} or any(char in value for char in "/\\\x00"):
            raise ValueError("document basename must be one non-traversing filename")
        return value


@dataclass(frozen=True, slots=True)
class FileDocumentText:
    """One complete bounded prefix with observed regular-file metadata."""

    info: FileMetadata
    prefix: FileTextResult


@dataclass(frozen=True, slots=True)
class FileDocumentFailure:
    """A candidate's ordered failure; scope and lifecycle failures still raise."""

    path: str
    phase: Literal["probe", "read"]
    error: EnvironmentError


type FileDocument = FileDocumentText | FileDocumentFailure


class FileEntriesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: tuple[FileMetadata, ...]
    offset: int = Field(ge=0)
    has_more: bool


class FileTreeRequest(BaseModel):
    """A bounded, metadata-only breadth-first observation, without retained cursors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: str
    max_depth: int = Field(default=4, ge=1, le=16)
    max_entries: int = Field(default=256, gt=0, le=4_096)
    max_directory_calls: int = Field(default=64, gt=0, le=1_024)
    include_hidden: bool = False
    max_bytes: int = Field(default=16 * 1024, gt=0, le=256 * 1024)
    # Encoded root-prefix length after path virtualization, excluding JSON quotes.
    # None selects the observed provider root. This is accounting, never a path grant.
    path_prefix_bytes: int | None = Field(default=None, ge=0)


class FileTreeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: tuple[FileMetadata, ...]
    truncated: bool
    directory_calls: int = Field(ge=0)


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


class _FileOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str


class FileStatOperation(_FileOperation):
    action: Literal["stat"] = "stat"
    missing_ok: bool = False


class FileMkdirOperation(_FileOperation):
    action: Literal["mkdir"] = "mkdir"
    parents: bool = False
    exist_ok: bool = False


class FileWriteTextOperation(_FileOperation):
    action: Literal["write_text"] = "write_text"
    text: str
    # Batches carry whole small texts; append remains an individual read/modify/write operation.
    mode: Literal["create", "replace", "upsert"]


class FileRemoveOperation(_FileOperation):
    action: Literal["remove"] = "remove"
    recursive: bool = False
    missing_ok: bool = False


type FileOperation = FileStatOperation | FileMkdirOperation | FileWriteTextOperation | FileRemoveOperation
type FileBatchValue = FileMetadata | FileMutationResult | FileWriteResult | None


class FileOperator(Protocol):
    async def list_tree(self, request: FileTreeRequest) -> FileTreeResult:
        """Observe sorted directory pages in BFS order under FILE_LIST authority.

        max_directory_calls bounds shallow observations, including continuation
        pages, independently of transport RPC count. max_bytes charges each
        compact UTF-8 JSON {kind,path,size?} plus one separator byte; paths use
        the observed root prefix or the declared encoded path_prefix_bytes.
        Stop before another directory observation after any budget is exhausted.
        """
        ...

    def discover_documents(self, request: FileDocumentRequest) -> AsyncIterator[FileDocument]:
        """Yield root-first then sorted child documents, without following child directories.

        Detect entry overflow before yielding (environment_too_large with reason
        entries_exceeded). Never treat partial or unrepresentable discovery as
        complete. Providers own bounded paging and complete-prefix assembly;
        callers must exhaust or close the iterator within its file scope.
        """
        ...

    async def batch(self, operations: Sequence[FileOperation]) -> tuple[FileBatchValue, ...]:
        """Run bounded operations in order in one file scope, stopping on error.

        `MAX_FILE_BATCH_OPERATIONS` and `MAX_FILE_BATCH_BYTES` (JSON) bound one batch.

        A failed or cancelled batch may have completed a prefix; it is not a transaction and is never replayed.
        """
        ...

    async def write_archive(self, path: str, archive: FileArchive) -> FileMutationResult:
        """Extract an exact ZIP manifest into an absent directory; the caller owns publication."""
        ...

    async def read_text(
        self,
        path: str,
        *,
        line_offset: int = 0,
        line_limit: int = 200,
        max_line_length: int = 2_000,
        max_bytes: int | None = None,
    ) -> FileTextResult:
        """Read logical lines under the optional positive UTF-8 text byte ceiling.

        Provider limits still apply. Continue with line_offset + lines_read;
        providers clip an oversized first line or report a size-limit error.
        """
        ...

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
    ) -> FileCopyResult:
        """Create the destination, permitting replacement of an existing file only when requested."""
        ...
