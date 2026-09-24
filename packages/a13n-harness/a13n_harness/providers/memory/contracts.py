"""The store contract behind file memory: versioned files with compare-and-swap writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

type MemoryAccess = Literal["read", "write"]
type MemoryErrorCode = Literal[
    "not_found",
    "already_exists",
    "version_mismatch",
    "invalid_path",
    "invalid_file",
    "invalid_pattern",
    "too_large",
    "memory_full",
    "memory_deleted",
    "forbidden",
    "unavailable",
]


class MemoryStoreError(Exception):
    """A store refused or could not complete an operation.

    `current` carries the file as it is now when a version check failed, or
    None when the file no longer exists.
    """

    def __init__(self, code: MemoryErrorCode, message: str, *, current: FileText | None = None) -> None:
        super().__init__(message)
        self.code: MemoryErrorCode = code
        self.current = current


@dataclass(frozen=True, slots=True)
class Origin:
    """Who a change is attributed to. Stores without history ignore it."""

    run_id: str | None = None
    principal_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    version: str
    size: int
    description: str | None


@dataclass(frozen=True, slots=True)
class FileText:
    path: str
    text: str
    version: str


@dataclass(frozen=True, slots=True)
class Changes:
    """The paths changed after the caller's cursor, and the cursor that covers them."""

    cursor: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FullResync:
    """The store cannot list changes since the caller's cursor; the caller reads everything as of `cursor`."""

    cursor: str


@dataclass(frozen=True, slots=True)
class GrepMatch:
    path: str
    line: int
    text: str


@runtime_checkable
class FileStore(Protocol):
    """One memory's files. Versions are opaque; every mutation is atomic and compare-and-swap.

    `write` with `expected=None` creates a file that must not exist yet. A failed
    check raises `version_mismatch` with the current file. `changes` returns a
    cursor taken before any content the caller reads next, so a change made
    meanwhile is listed again rather than skipped.
    """

    async def list(self) -> list[FileEntry]: ...

    async def read(self, path: str) -> FileText: ...

    async def write(self, path: str, text: str, *, expected: str | None, origin: Origin) -> str: ...

    async def move(self, source: str, destination: str, *, expected: str, origin: Origin) -> str: ...

    async def delete(self, path: str, *, expected: str, origin: Origin) -> None: ...

    async def changes(self, since: str | None) -> Changes | FullResync: ...

    async def purge(self) -> None:
        """Remove every file of the memory."""
        ...


@runtime_checkable
class SearchableFileStore(FileStore, Protocol):
    """A store that matches lines itself; callers scan the files of any other store."""

    async def search(
        self, pattern: str, *, regex: bool, case_sensitive: bool, path: str, limit: int
    ) -> tuple[list[GrepMatch], bool]:
        """Matching lines under `path` ("" or a directory ending in "/"), and whether more matched."""
        ...
