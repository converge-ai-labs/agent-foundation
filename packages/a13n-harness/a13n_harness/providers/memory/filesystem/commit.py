"""Stage a memory operation and publish through native conditional file commit."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import PurePosixPath

from a13n_harness.providers.environment.files import (
    FileCommitCondition,
    FileCommitOperator,
    FileCommitRequest,
    FileCommitWrite,
)
from a13n_harness.providers.environment.models import EnvironmentError

from ..documents import MemoryDocumentError
from .store import MemoryFileReader, MemoryFileTransaction, MemoryTransactionFiles

_MAX_FILE_BYTES = 2 * 1024 * 1024


class _Transaction:
    def __init__(self, files: MemoryFileReader, root: str) -> None:
        self.backend = files
        self.root = root
        self.original: dict[str, bytes | None] = {}
        self.writes: dict[str, str] = {}
        self.directories: dict[str, None] = {}
        self.removals: dict[str, None] = {}

    @property
    def files(self) -> MemoryTransactionFiles:
        return self

    def _path(self, path: str) -> None:
        parsed = PurePosixPath(path)
        if str(parsed) != path or ".." in parsed.parts or not path.startswith(self.root + "/"):
            raise MemoryDocumentError("memory_storage_unavailable")

    async def _original(self, path: str) -> bytes | None:
        self._path(path)
        if path not in self.original:
            try:
                raw = await self.backend.read_bytes(path, length=_MAX_FILE_BYTES + 1)
            except EnvironmentError as error:
                if error.code != "environment_not_found":
                    raise
                raw = None
            if raw is not None and len(raw) > _MAX_FILE_BYTES:
                raise MemoryDocumentError("memory_document_too_large")
            if len(self.original) >= 240:
                raise MemoryDocumentError("memory_history_limit")
            self.original[path] = raw
        return self.original[path]

    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
        self._path(path)
        if any(path == removed or path.startswith(removed + "/") for removed in self.removals):
            raise EnvironmentError("File is absent", code="environment_not_found")
        raw = self.writes[path].encode() if path in self.writes else await self._original(path)
        if raw is None:
            raise EnvironmentError("File is absent", code="environment_not_found")
        return raw[offset:] if length is None else raw[offset : offset + length]

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> None:
        self._path(path)
        if not parents or not exist_ok:
            raise MemoryDocumentError("memory_write_unsupported")
        self.directories[path] = None

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        self._path(path)
        if not recursive:
            raise MemoryDocumentError("memory_write_unsupported")
        self.removals[path] = None

    async def publish(self, path: str, text: str, *, expected: bytes | None) -> None:
        current = self.writes[path].encode() if path in self.writes else await self._original(path)
        if current != expected:
            raise MemoryDocumentError("memory_conflict")
        self.writes[path] = text

    async def commit(self) -> None:
        if not (self.writes or self.directories or self.removals):
            return
        if not isinstance(self.backend, FileCommitOperator):
            raise MemoryDocumentError("memory_write_unsupported")
        try:
            await self.backend.commit(
                FileCommitRequest(
                    root=self.root,
                    conditions=tuple(
                        FileCommitCondition(path=path, digest=sha256(raw).hexdigest() if raw is not None else None)
                        for path, raw in self.original.items()
                    ),
                    directories=tuple(self.directories),
                    writes=tuple(FileCommitWrite(path=path, text=text) for path, text in self.writes.items()),
                    removals=tuple(self.removals),
                )
            )
        except EnvironmentError as error:
            code = {
                "environment_conflict": "memory_conflict",
                "environment_unsupported": "memory_write_unsupported",
            }.get(error.code, "memory_write_unconfirmed")
            raise MemoryDocumentError(code) from error


class EnvironmentMemoryFileCoordinator:
    """One exact corpus, with no process-local or ordinary-write fallback."""

    def __init__(self, files: MemoryFileReader, *, root: str, store_id: str, scope: str) -> None:
        self.files, self.root, self.store_id, self.scope = files, root, store_id, scope

    @asynccontextmanager
    async def transaction(self, store_id: str, scope: str) -> AsyncIterator[MemoryFileTransaction]:
        if (store_id, scope) != (self.store_id, self.scope):
            raise MemoryDocumentError("memory_storage_unavailable")
        transaction = _Transaction(self.files, self.root)
        yield transaction
        await transaction.commit()
