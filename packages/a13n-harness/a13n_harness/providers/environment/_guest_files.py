"""Guest filesystem operations with bounded transfers and shared patch semantics."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import tempfile
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from io import BytesIO
from pathlib import PurePosixPath

from pydantic import JsonValue

from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.batches import validate_file_batch
from a13n_harness.environment.files import (
    FileBatchValue,
    FileCopyResult,
    FileEntriesResult,
    FileMetadata,
    FileMutationResult,
    FileOperation,
    FilePatchResult,
    FileQueryRequest,
    FileRemoveOperation,
    FileStatOperation,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
    FileWriteTextOperation,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.text import apply_unified_diff, iter_lf_lines
from a13n_harness.providers.environment._guest_commands import FileCommands, decoded_bytes


class GuestFiles:
    def __init__(self, commands: FileCommands) -> None:
        self.commands = commands

    async def batch(self, operations: Sequence[FileOperation]) -> tuple[FileBatchValue, ...]:
        validate_file_batch(operations)
        if not operations:
            return ()
        response = await self.commands.files(
            "batch",
            {"operations": [item.model_dump(mode="json") for item in operations]},
            mutation=any(not isinstance(item, FileStatOperation) for item in operations),
        )
        values = response.get("results")
        if not isinstance(values, list) or len(values) != len(operations):
            raise EnvironmentError("Invalid file batch response.", code="environment_provider_failure")
        results: list[FileBatchValue] = []
        for operation, value in zip(operations, values, strict=True):
            if (
                value is None
                and isinstance(operation, (FileStatOperation, FileRemoveOperation))
                and operation.missing_ok
            ):
                results.append(None)
                continue
            if not isinstance(value, dict):
                raise EnvironmentError("Invalid file batch response.", code="environment_provider_failure")
            if isinstance(operation, FileStatOperation):
                results.append(FileMetadata.model_validate(value))
            elif isinstance(operation, FileWriteTextOperation):
                results.append(
                    FileWriteResult.model_validate(
                        {
                            **value,
                            "path": operation.path,
                            "receipt": self.commands.receipt(),
                        }
                    )
                )
            else:
                results.append(FileMutationResult(path=operation.path, receipt=self.commands.receipt()))
        return tuple(results)

    async def write_archive(self, path: str, archive: FileArchive) -> FileMutationResult:
        maximum = self.commands.configuration.max_file_bytes
        if len(archive.content) > maximum or any(size > maximum for _, size in archive.entries):
            raise EnvironmentError("Archive exceeds the configured limit.", code="environment_too_large")
        staged = str(PurePosixPath(path).parent / f".a13n-archive-{secrets.token_hex(12)}")
        value = await self.commands.files("stage", {"path": staged, "parents": True}, mutation=True)
        native = value.get("path")
        if not isinstance(native, str):
            raise EnvironmentError("Guest path resolution failed.", code="environment_provider_failure")
        # The guest owns cleanup once extraction starts. An unconfirmed upload can leave only a private ZIP.
        with BytesIO(archive.content) as source:
            await self.commands.write_stream(native, source)
        return await self._mutate(
            "archive",
            {
                "path": path,
                "staged": staged,
                "digest": hashlib.sha256(archive.content).hexdigest(),
                "root": archive.root,
                "entries": [[name, size] for name, size in archive.entries],
            },
        )

    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
        maximum = self.commands.configuration.max_file_bytes
        if offset < 0 or (length is not None and (length < 0 or length > maximum)):
            raise EnvironmentError("Invalid Guest read range.", code="environment_request_invalid")
        result = bytearray()
        if offset == 0 and length is None:
            async with closing_stream(self.read_bytes_stream(path)) as stream:
                async for chunk in stream:
                    result.extend(chunk)
            return bytes(result)
        # Keep ranged reads seek-based: the native download API has no range
        # support, and discarding an arbitrary prefix would amplify transfer I/O.
        needed = length if length is not None else maximum + 1
        while len(result) < needed:
            size = min(65536, needed - len(result))
            chunk = decoded_bytes(
                await self.commands.files("read", {"path": path, "offset": offset + len(result), "length": size})
            )
            result.extend(chunk)
            if len(chunk) < size:
                break
        if len(result) > maximum:
            raise EnvironmentError("File exceeds the configured limit.", code="environment_too_large")
        return bytes(result)

    async def read_bytes_stream(self, path: str, *, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        if chunk_size <= 0 or chunk_size > 1024 * 1024:
            raise EnvironmentError("Invalid Guest chunk size.", code="environment_request_invalid")
        native = await self._resolve_native_path(path, regular_file=True)
        total = 0
        async with closing_stream(self.commands.read_stream(native)) as stream:
            async for chunk in stream:
                total += len(chunk)
                if total > self.commands.configuration.max_file_bytes:
                    raise EnvironmentError("File exceeds the configured limit.", code="environment_too_large")
                for offset in range(0, len(chunk), chunk_size):
                    yield chunk[offset : offset + chunk_size]

    async def _resolve_native_path(self, path: str, *, regular_file: bool = False) -> str:
        value = (await self.commands.files("resolve", {"path": path, "regular_file": regular_file})).get("path")
        if not isinstance(value, str):
            raise EnvironmentError("Guest path resolution failed.", code="environment_provider_failure")
        return value

    async def _create_upload_stage(self, path: str) -> str:
        value = (await self.commands.files("stage", {"path": path}, mutation=True)).get("path")
        if not isinstance(value, str):
            raise EnvironmentError("Guest path resolution failed.", code="environment_provider_failure")
        return value

    async def read_text(
        self, path: str, *, line_offset: int = 0, line_limit: int = 200, max_line_length: int = 2000
    ) -> FileTextResult:
        if line_offset < 0 or line_limit <= 0 or max_line_length <= 0:
            raise EnvironmentError("Invalid Guest text range.", code="environment_request_invalid")
        text = _text(await self.read_bytes(path))
        lines = list(iter_lf_lines(text))
        selected = lines[line_offset : line_offset + line_limit]
        truncated = tuple(
            line_offset + index + 1 for index, line in enumerate(selected) if len(line.rstrip("\r\n")) > max_line_length
        )
        rendered = [
            line
            if len(line.rstrip("\r\n")) <= max_line_length
            else line[:max_line_length] + ("\n" if line.endswith("\n") else "")
            for line in selected
        ]
        return FileTextResult(
            path=path,
            text="".join(rendered),
            line_offset=line_offset,
            lines_read=len(selected),
            has_more=len(lines) > line_offset + line_limit,
            truncated_lines=truncated,
        )

    async def write_bytes_stream(
        self, path: str, stream: AsyncIterable[bytes], *, mode: FileWriteMode
    ) -> FileWriteResult:
        if mode not in {"create", "replace", "upsert", "append"}:
            raise EnvironmentError("Invalid Guest write mode.", code="environment_request_invalid")
        # Keep local memory bounded even for SDK uploads; publish only a finished transfer.
        staged = str(PurePosixPath(path).parent / f".a13n-write-{secrets.token_hex(12)}")
        await self._resolve_native_path(staged)
        size = 0
        maximum = self.commands.configuration.max_file_bytes
        with tempfile.TemporaryFile("w+b") as file:
            if mode == "append":
                try:
                    previous = await self.read_bytes(path)
                except EnvironmentError as error:
                    if error.code != "environment_not_found":
                        raise
                    previous = b""
                await asyncio.to_thread(file.write, previous)
                size = len(previous)
            original_size = size
            async for chunk in stream:
                if not isinstance(chunk, bytes):
                    raise TypeError("Guest file chunks must be bytes")
                size += len(chunk)
                if size > maximum:
                    raise EnvironmentError("File exceeds the configured limit.", code="environment_too_large")
                await asyncio.to_thread(file.write, chunk)
            await asyncio.to_thread(file.seek, 0)
            stage_created = False
            try:
                native = await self._create_upload_stage(staged)
                stage_created = True
                await self.commands.write_stream(native, file)
                await self.commands.files(
                    "publish",
                    {"path": path, "staged": staged, "mode": "upsert" if mode == "append" else mode},
                    mutation=True,
                )
            except BaseException as error:
                if (
                    not stage_created
                    and isinstance(error, EnvironmentError)
                    and error.code
                    in {
                        "environment_conflict",
                        "environment_denied",
                        "environment_not_found",
                        "environment_request_invalid",
                    }
                ):
                    # A rejected exclusive creation does not authorize deleting that entry.
                    raise
                try:
                    await asyncio.shield(
                        self.commands.files("remove", {"path": staged, "recursive": False}, mutation=True)
                    )
                except EnvironmentError as cleanup_error:
                    if cleanup_error.code != "environment_not_found":
                        error.add_note("Guest staged upload cleanup failed.")
                except Exception:
                    error.add_note("Guest staged upload cleanup could not be confirmed.")
                raise
        return FileWriteResult(path=path, bytes_written=size - original_size, receipt=self.commands.receipt())

    async def write_text(self, path: str, text: str, *, mode: FileWriteMode) -> FileWriteResult:
        if "\x00" in text:
            raise EnvironmentError("Text contains NUL.", code="environment_unsupported")
        if mode == "append":
            try:
                _text(await self.read_bytes(path))
            except EnvironmentError as error:
                if error.code != "environment_not_found":
                    raise

        async def content() -> AsyncIterator[bytes]:
            yield text.encode("utf-8")

        return await self.write_bytes_stream(path, content(), mode=mode)

    async def patch_text(self, path: str, patch: str) -> FilePatchResult:
        updated, count = apply_unified_diff(
            _text(await self.read_bytes(path)), patch, max_result_bytes=self.commands.configuration.max_file_bytes
        )
        result = await self.write_text(path, updated, mode="replace")
        return FilePatchResult(path=path, hunks_applied=count, receipt=result.receipt)

    async def stat(self, path: str) -> FileMetadata:
        return FileMetadata.model_validate(await self.commands.files("stat", {"path": path}))

    async def list(
        self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False
    ) -> FileEntriesResult:
        if offset < 0 or max_results <= 0:
            raise EnvironmentError("Invalid Guest listing range.", code="environment_request_invalid")
        return FileEntriesResult.model_validate(
            await self.commands.files(
                "list", {"path": path, "offset": offset, "max_results": max_results, "include_hidden": include_hidden}
            )
        )

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        return FileEntriesResult.model_validate(
            await self.commands.files("query", {**request.model_dump(mode="json"), "path": request.root})
        )

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        return FileTextSearchResult.model_validate(
            await self.commands.files("search", {**request.model_dump(mode="json"), "path": request.root})
        )

    async def _mutate(self, action: str, arguments: dict[str, JsonValue]) -> FileMutationResult:
        await self.commands.files(action, arguments, mutation=True)
        return FileMutationResult(
            path=str(arguments.get("destination", arguments["path"])), receipt=self.commands.receipt()
        )

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> FileMutationResult:
        return await self._mutate("mkdir", {"path": path, "parents": parents, "exist_ok": exist_ok})

    async def move(self, source: str, destination: str, *, replace: bool = False) -> FileMutationResult:
        return await self._mutate("move", {"path": source, "destination": destination, "replace": replace})

    async def remove(self, path: str, *, recursive: bool = False) -> FileMutationResult:
        return await self._mutate("remove", {"path": path, "recursive": recursive})

    async def copy(self, source: str, destination: str, *, replace: bool = False) -> FileCopyResult:
        result = await self.write_bytes_stream(
            destination, self.read_bytes_stream(source), mode="upsert" if replace else "create"
        )
        return FileCopyResult(path=destination, bytes_copied=result.bytes_written, receipt=result.receipt)


def _text(data: bytes) -> str:
    try:
        if b"\x00" in data:
            raise ValueError("NUL")
        return data.decode("utf-8")
    except ValueError:
        raise EnvironmentError("File is not UTF-8 text.", code="environment_unsupported") from None
