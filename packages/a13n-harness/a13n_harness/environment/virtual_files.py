"""Stable virtual-path file facade over current aggregate routing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from .files import (
    FileCopyResult,
    FileEntriesResult,
    FileMetadata,
    FileMutationResult,
    FilePatchResult,
    FileQueryRequest,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
)
from .models import EnvironmentAction, EnvironmentError, EnvironmentPath


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    selected: EnvironmentPath
    observed_generation: str
    backend: Any
    validate_result: Callable[[Any], None]
    virtualize_path: Callable[[str], str]


ResolvePath = Callable[[str], EnvironmentPath]
PrepareFile = Callable[
    [EnvironmentPath, EnvironmentAction],
    AbstractAsyncContextManager[_PreparedFile],
]


class VirtualFileOperator:
    def __init__(self, resolve: ResolvePath, prepare: PrepareFile) -> None:
        self._resolve = resolve
        self._prepare = prepare

    async def read_text(self, path: str, **kwargs: Any) -> FileTextResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_READ_TEXT) as prepared:
            result = await prepared.backend.read_text(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_READ_BYTES) as prepared:
            return await prepared.backend.read_bytes(
                prepared.selected.path,
                offset=offset,
                length=length,
            )

    async def read_bytes_stream(
        self,
        path: str,
        *,
        chunk_size: int = 65_536,
    ) -> AsyncGenerator[bytes]:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_READ_BYTES) as prepared:
            async for chunk in prepared.backend.read_bytes_stream(
                prepared.selected.path,
                chunk_size=chunk_size,
            ):
                yield chunk

    async def write_bytes_stream(
        self,
        path: str,
        stream: AsyncIterable[bytes],
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_WRITE_BYTES) as prepared:
            result = await prepared.backend.write_bytes_stream(
                prepared.selected.path,
                stream,
                mode=mode,
            )
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_WRITE_TEXT) as prepared:
            result = await prepared.backend.write_text(
                prepared.selected.path,
                text,
                mode=mode,
            )
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def patch_text(
        self,
        path: str,
        patch: str,
    ) -> FilePatchResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_PATCH_TEXT) as prepared:
            result = await prepared.backend.patch_text(
                prepared.selected.path,
                patch,
            )
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def stat(self, path: str) -> FileMetadata:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_STAT) as prepared:
            result = await prepared.backend.stat(prepared.selected.path)
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def list(self, path: str, **kwargs: Any) -> FileEntriesResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_LIST) as prepared:
            result = await prepared.backend.list(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            entries = tuple(
                entry.model_copy(update={"path": prepared.virtualize_path(entry.path)}) for entry in result.entries
            )
            return result.model_copy(update={"entries": entries})

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        async with self._prepare(self._resolve(request.root), EnvironmentAction.FILE_QUERY) as prepared:
            result = await prepared.backend.query(request.model_copy(update={"root": prepared.selected.path}))
            prepared.validate_result(result)
            entries = tuple(
                entry.model_copy(update={"path": prepared.virtualize_path(entry.path)}) for entry in result.entries
            )
            return result.model_copy(update={"entries": entries})

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        async with self._prepare(self._resolve(request.root), EnvironmentAction.FILE_SEARCH_TEXT) as prepared:
            result = await prepared.backend.search_text(request.model_copy(update={"root": prepared.selected.path}))
            prepared.validate_result(result)
            matches = tuple(
                match.model_copy(update={"path": prepared.virtualize_path(match.path)}) for match in result.matches
            )
            return result.model_copy(update={"matches": matches})

    async def mkdir(self, path: str, **kwargs: Any) -> FileMutationResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_MKDIR) as prepared:
            result = await prepared.backend.mkdir(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def move(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileMutationResult:
        source_selected = self._resolve(source)
        destination_selected = self._resolve(destination)
        async with self._prepare(source_selected, EnvironmentAction.FILE_MOVE) as source_file:
            async with self._prepare(destination_selected, EnvironmentAction.FILE_MOVE) as destination_file:
                if (
                    source_file.selected.mount_id != destination_file.selected.mount_id
                    or source_file.backend is not destination_file.backend
                ):
                    raise EnvironmentError(
                        "Cross-mount move must be expressed as copy and separately authorized remove.",
                        code="environment_unsupported",
                    )
                result = await source_file.backend.move(
                    source_file.selected.path,
                    destination_file.selected.path,
                    replace=replace,
                )
                destination_file.validate_result(result)
                return result.model_copy(update={"path": destination})

    async def remove(self, path: str, **kwargs: Any) -> FileMutationResult:
        async with self._prepare(self._resolve(path), EnvironmentAction.FILE_REMOVE) as prepared:
            result = await prepared.backend.remove(prepared.selected.path, **kwargs)
            prepared.validate_result(result)
            return result.model_copy(update={"path": path})

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileCopyResult:
        return await self._copy_resolved(
            source,
            destination,
            source_selected=self._resolve(source),
            destination_selected=self._resolve(destination),
            replace=replace,
        )

    async def _copy_resolved(
        self,
        source: str,
        destination: str,
        *,
        source_selected: EnvironmentPath,
        destination_selected: EnvironmentPath,
        replace: bool,
    ) -> FileCopyResult:
        """Copy through exact preselected routes using copy-specific actions."""
        async with self._prepare(source_selected, EnvironmentAction.FILE_COPY_SOURCE) as source_file:
            async with self._prepare(
                destination_selected,
                EnvironmentAction.FILE_COPY_DESTINATION,
            ) as destination_file:
                if (
                    source_file.selected.mount_id == destination_file.selected.mount_id
                    and source_file.backend is destination_file.backend
                ):
                    result = await source_file.backend.copy(
                        source_file.selected.path,
                        destination_file.selected.path,
                        replace=replace,
                    )
                    destination_file.validate_result(result)
                    return result.model_copy(update={"path": destination})

                stream = source_file.backend.read_bytes_stream(source_file.selected.path)
                written = await destination_file.backend.write_bytes_stream(
                    destination_file.selected.path,
                    stream,
                    mode="replace" if replace else "create",
                )
                destination_file.validate_result(written)
                result = FileCopyResult(
                    path=destination,
                    bytes_copied=written.bytes_written,
                    receipt=written.receipt,
                )
                destination_file.validate_result(result)
                return result
