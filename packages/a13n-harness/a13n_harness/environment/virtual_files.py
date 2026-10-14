"""Stable virtual-path file facade over current aggregate routing."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from typing import Any

from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.batches import file_operation_action, validate_file_batch
from a13n_harness.environment.files import (
    FileBatchValue,
    FileCopyResult,
    FileEntriesResult,
    FileMetadata,
    FileMutationResult,
    FileOperation,
    FilePatchResult,
    FileQueryRequest,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
)
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.environment.mounts import EnvironmentPath
from a13n_harness.environment.providers import FileScopeSelection


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    selected: EnvironmentPath
    backend: Any
    validate_result: Callable[[Any], None]
    virtualize_path: Callable[[str], str]


ResolvePath = Callable[[str], FileScopeSelection]
PrepareFile = Callable[
    [FileScopeSelection, EnvironmentAction],
    AbstractAsyncContextManager[_PreparedFile],
]


class VirtualFileOperator:
    def __init__(self, resolve: ResolvePath, prepare: PrepareFile) -> None:
        self._resolve = resolve
        self._prepare = prepare

    async def batch(self, operations: Sequence[FileOperation]) -> tuple[FileBatchValue, ...]:
        validate_file_batch(operations)
        if not operations:
            return ()
        selections = [self._resolve(item.path) for item in operations]
        first_selection = selections[0]
        if any(
            item.resolved_path.mount_id != first_selection.resolved_path.mount_id
            or item.resolved_path.execution_id != first_selection.resolved_path.execution_id
            or item.observed_generation != first_selection.observed_generation
            for item in selections
        ):
            raise EnvironmentError("File batch crosses a file scope.", code="environment_selection_invalid")
        async with AsyncExitStack() as stack:
            prepared = [
                await stack.enter_async_context(self._prepare(selected, file_operation_action(item)))
                for selected, item in zip(selections, operations, strict=True)
            ]
            routed = [
                item.model_copy(update={"path": route.selected.path})
                for item, route in zip(operations, prepared, strict=True)
            ]
            results = await prepared[0].backend.batch(routed)
            if len(results) != len(operations):
                raise EnvironmentError("Invalid file batch response.", code="environment_provider_failure")
            values: list[FileBatchValue] = []
            for item, route, result in zip(operations, prepared, results, strict=True):
                if result is not None:
                    route.validate_result(result)
                    result = result.model_copy(update={"path": item.path})
                values.append(result)
            return tuple(values)

    async def write_archive(self, path: str, archive: FileArchive) -> FileMutationResult:
        selected = self._resolve(path)
        for name, _ in archive.entries:
            child = self._resolve(f"{path.rstrip('/')}/{name}")
            expected = selected.resolved_path.model_copy(
                update={"path": f"{selected.resolved_path.path.rstrip('/')}/{name}"}
            )
            if child.resolved_path != expected or child.observed_generation != selected.observed_generation:
                raise EnvironmentError("Archive crosses a file scope.", code="environment_selection_invalid")
        async with self._prepare(selected, EnvironmentAction.FILE_MKDIR):
            async with self._prepare(selected, EnvironmentAction.FILE_WRITE_BYTES) as prepared:
                result = await prepared.backend.write_archive(prepared.selected.path, archive)
                prepared.validate_result(result)
                return result.model_copy(update={"path": path})

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
            async with closing_stream(
                prepared.backend.read_bytes_stream(prepared.selected.path, chunk_size=chunk_size)
            ) as stream:
                async for chunk in stream:
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
        source_selected: FileScopeSelection,
        destination_selected: FileScopeSelection,
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
                    mode="upsert" if replace else "create",
                )
                destination_file.validate_result(written)
                result = FileCopyResult(
                    path=destination,
                    bytes_copied=written.bytes_written,
                    receipt=written.receipt,
                )
                destination_file.validate_result(result)
                return result
