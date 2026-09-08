"""Direct Local root-confined filesystem implementation."""

from __future__ import annotations

import asyncio
import codecs
import itertools
import os
import shutil
import stat as stat_module
import sys
import tempfile
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pathspec.gitignore import GitIgnoreSpec

from .._file_patterns import PathPattern, PatternError, content_pattern
from .._file_search import search_text_file
from ..files import (
    FileCopyResult,
    FileEntriesResult,
    FileKind,
    FileMetadata,
    FileMutationResult,
    FilePatchResult,
    FileQueryRequest,
    FileTextMatch,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
    FileWriteMode,
    FileWriteResult,
)
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..text import apply_unified_diff

if TYPE_CHECKING:
    from .provider import _DirectLocalFilePolicy


class _LocalWriter:
    def __init__(
        self,
        operator: LocalFileOperator,
        logical_path: str,
        native_path: Path,
        mode: FileWriteMode,
        *,
        validate_text_append: bool,
    ) -> None:
        self._operator = operator
        self._logical_path = logical_path
        self._native_path = native_path
        self._mode: FileWriteMode = mode
        self._validate_text_append = validate_text_append
        self._temp_path: Path | None = None
        self._file: Any | None = None
        self._payload_bytes = 0
        self._committed = False

    async def open(self) -> None:
        try:
            fd, name = await asyncio.to_thread(
                tempfile.mkstemp,
                prefix=".a13n-write-",
                dir=self._native_path.parent,
            )
        except OSError as exc:
            raise _environment_error_from_os(exc, action="open a staged file") from exc
        self._temp_path = Path(name)
        self._file = os.fdopen(fd, "wb")
        if self._mode == "append" and await asyncio.to_thread(self._native_path.exists):
            await self._copy_append_source()

    async def _copy_append_source(self) -> None:
        try:
            source = await asyncio.to_thread(self._native_path.open, "rb")
        except OSError as exc:
            raise _environment_error_from_os(exc, action="open an append source") from exc
        decoder = codecs.getincrementaldecoder("utf-8")("strict") if self._validate_text_append else None
        try:
            try:
                while chunk := await asyncio.to_thread(source.read, 65_536):
                    if decoder is not None:
                        if b"\x00" in chunk:
                            raise EnvironmentError(
                                "Text append source contains NUL.",
                                code="environment_unsupported",
                            )
                        try:
                            decoder.decode(chunk, final=False)
                        except UnicodeDecodeError as exc:
                            raise EnvironmentError(
                                "Text append source is not valid UTF-8.",
                                code="environment_unsupported",
                            ) from exc
                    await self._write(chunk, payload=False)
                if decoder is not None:
                    try:
                        decoder.decode(b"", final=True)
                    except UnicodeDecodeError as exc:
                        raise EnvironmentError(
                            "Text append source is not valid UTF-8.",
                            code="environment_unsupported",
                        ) from exc
            except OSError as exc:
                raise _environment_error_from_os(exc, action="read an append source") from exc
        finally:
            try:
                await asyncio.to_thread(source.close)
            except OSError:
                pass

    async def write(self, chunk: bytes) -> None:
        await self._write(chunk, payload=True)

    async def _write(self, chunk: bytes, *, payload: bool) -> None:
        if self._file is None or self._committed:
            raise EnvironmentError("File writer is not writable.", code="environment_conflict")
        if not isinstance(chunk, bytes):
            raise TypeError("raw writer chunks must be bytes")
        try:
            await asyncio.to_thread(self._file.write, chunk)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="write a staged file") from exc
        if payload:
            self._payload_bytes += len(chunk)

    async def commit(self) -> FileWriteResult:
        if self._file is None or self._temp_path is None or self._committed:
            raise EnvironmentError("File writer cannot commit.", code="environment_conflict")
        try:
            await asyncio.to_thread(self._file.flush)
            await asyncio.to_thread(os.fsync, self._file.fileno())
            await asyncio.to_thread(self._file.close)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="commit a staged file") from exc
        self._file = None
        publication = asyncio.create_task(
            asyncio.to_thread(
                self._operator._publish_staged,
                self._temp_path,
                self._native_path,
                self._mode,
            )
        )
        while not publication.done():
            try:
                await asyncio.shield(publication)
            except asyncio.CancelledError:
                continue
        publication.result()
        self._temp_path = None
        self._committed = True
        return FileWriteResult(
            path=self._logical_path,
            bytes_written=self._payload_bytes,
            receipt=self._operator._receipt(),
        )

    async def abort(self) -> None:
        failure: EnvironmentError | None = None
        if self._file is not None:
            try:
                await asyncio.to_thread(self._file.close)
            except OSError as exc:
                failure = _environment_error_from_os(exc, action="close a staged file")
            finally:
                self._file = None
        if self._temp_path is not None:
            try:
                await asyncio.to_thread(self._temp_path.unlink, missing_ok=True)
            except OSError as exc:
                cleanup = _environment_error_from_os(exc, action="remove a staged file")
                if failure is None:
                    failure = cleanup
                else:
                    failure.add_note(f"Staged file removal also failed: {cleanup!r}")
            finally:
                self._temp_path = None
        if failure is not None:
            raise failure


class LocalFileOperator:
    """Root-confined Direct Local file operations with staged publication."""

    def __init__(
        self,
        *,
        root: Path,
        read_only: bool,
        policy: _DirectLocalFilePolicy,
        mount_id: str,
        generation: str,
    ) -> None:
        self._root = root.resolve(strict=True)
        self._read_only = read_only
        self._policy = policy
        self._mount_id = mount_id
        self._generation = generation
        self._operations = itertools.count(1)

    def bind_mount(self, mount_id: str) -> None:
        self._mount_id = mount_id

    def _receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            mount_id=self._mount_id,
            observed_generation=self._generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    def _logical(self, native: Path) -> str:
        relative = native.relative_to(self._root)
        return "/" if not relative.parts else f"/{relative.as_posix()}"

    def _lexical(self, path: str) -> tuple[str, ...]:
        if not path.startswith("/") or "\x00" in path:
            raise EnvironmentError("Provider-local file paths must be absolute.", code="environment_request_invalid")
        pure = PurePosixPath(path)
        if any(part in {".", ".."} for part in pure.parts):
            raise EnvironmentError("File path traversal is invalid.", code="environment_request_invalid")
        return tuple(part for part in pure.parts if part != "/")

    def _resolve(self, path: str, *, follow_final: bool = True, require_exists: bool = True) -> Path:
        parts = self._lexical(path)
        candidate = self._root.joinpath(*parts)
        try:
            if follow_final:
                if require_exists:
                    resolved = candidate.resolve(strict=True)
                else:
                    parent = candidate.parent.resolve(strict=True)
                    resolved = parent / candidate.name
            elif candidate == self._root:
                resolved = self._root
            else:
                parent = candidate.parent.resolve(strict=require_exists)
                resolved = parent / candidate.name
        except OSError as exc:
            raise _environment_error_from_os(exc, action="resolve a file path") from exc
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise EnvironmentError("File path escapes the configured root.", code="environment_denied") from None
        return resolved

    async def resolve_native_directory(self, path: str) -> Path:
        """Resolve a provider-local cwd without exposing native-path fallback publicly."""
        return await asyncio.to_thread(self._resolve_directory, path, "Command cwd")

    def _resolve_directory(self, path: str, subject: str) -> Path:
        native = self._resolve(path)
        try:
            is_directory = native.is_dir()
        except OSError as exc:
            raise _environment_error_from_os(exc, action="inspect a directory") from exc
        if not is_directory:
            raise EnvironmentError(f"{subject} is not a directory.", code="environment_request_invalid")
        return native

    def _resolve_file(self, path: str, subject: str) -> Path:
        native = self._resolve(path)
        try:
            is_file = native.is_file()
        except OSError as exc:
            raise _environment_error_from_os(exc, action="inspect a file") from exc
        if not is_file:
            raise EnvironmentError(f"{subject} is not a regular file.", code="environment_request_invalid")
        return native

    def _require_writable(self, path: Path) -> None:
        if self._read_only:
            raise EnvironmentError("Direct Local root is read-only.", code="environment_denied")
        if path == self._root:
            raise EnvironmentError("The provider root cannot be mutated.", code="environment_denied")

    @staticmethod
    def _validate_writer_destination(path: Path) -> None:
        try:
            destination = path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _environment_error_from_os(exc, action="inspect a write destination") from exc
        if stat_module.S_ISLNK(destination.st_mode):
            raise EnvironmentError("Writing through a symlink is denied.", code="environment_denied")
        if not stat_module.S_ISREG(destination.st_mode):
            raise EnvironmentError("Destination is not a regular file.", code="environment_request_invalid")

    def _publish_staged(
        self,
        staged: Path,
        destination: Path,
        mode: FileWriteMode,
    ) -> None:
        self._require_writable(destination)
        exists = destination.exists() or destination.is_symlink()
        if destination.is_symlink():
            raise EnvironmentError("Writing through a symlink is denied.", code="environment_denied")
        if exists and destination.is_dir():
            raise EnvironmentError("Destination is a directory.", code="environment_request_invalid")
        if mode in {"replace", "append"} and not exists:
            raise EnvironmentError("Destination does not exist.", code="environment_not_found")
        try:
            parent = destination.parent.resolve(strict=True)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="resolve a destination parent") from exc
        try:
            parent.relative_to(self._root)
        except ValueError:
            raise EnvironmentError(
                "Destination parent escaped the configured root.", code="environment_denied"
            ) from None
        try:
            if mode == "create":
                os.link(staged, destination)
            else:
                os.replace(staged, destination)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="publish a staged file") from exc
        if mode == "create":
            try:
                staged.unlink()
            except OSError:
                pass

    async def read_text(
        self,
        path: str,
        *,
        line_offset: int = 0,
        line_limit: int = 200,
        max_line_length: int = 2_000,
    ) -> FileTextResult:
        if line_offset < 0:
            raise EnvironmentError("line_offset must not be negative.", code="environment_request_invalid")
        if line_limit < 1:
            raise EnvironmentError("line_limit must be positive.", code="environment_request_invalid")
        if max_line_length < 1:
            raise EnvironmentError("max_line_length must be positive.", code="environment_request_invalid")
        if line_limit * max_line_length > self._policy.max_value_bytes:
            raise EnvironmentError("Requested text page exceeds configured limit.", code="environment_too_large")
        native = await asyncio.to_thread(self._resolve_file, path, "Path")
        try:
            text, lines_read, has_more, truncated_lines = await asyncio.to_thread(
                _read_text_page,
                native,
                line_offset,
                line_limit,
                max_line_length,
            )
        except UnicodeDecodeError as exc:
            raise EnvironmentError(
                "Text source is not valid UTF-8.",
                code="environment_unsupported",
            ) from exc
        except OSError as exc:
            raise _environment_error_from_os(exc, action="read a text file") from exc
        if len(text.encode("utf-8")) > self._policy.max_value_bytes:
            raise EnvironmentError("Text page exceeds configured limit.", code="environment_too_large")
        return FileTextResult(
            path=path,
            text=text,
            line_offset=line_offset,
            lines_read=lines_read,
            has_more=has_more,
            truncated_lines=truncated_lines,
        )

    async def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        if offset < 0 or (length is not None and length < 0):
            raise EnvironmentError("Invalid byte read range.", code="environment_request_invalid")
        native = await asyncio.to_thread(self._resolve_file, path, "Raw read source")
        try:
            size = (await asyncio.to_thread(native.stat)).st_size
        except OSError as exc:
            raise _environment_error_from_os(exc, action="inspect a raw read source") from exc
        selected_bytes = max(size - offset, 0)
        if length is not None:
            selected_bytes = min(selected_bytes, length)
        if selected_bytes > self._policy.max_value_bytes:
            raise EnvironmentError("Raw read exceeds value limit.", code="environment_too_large")
        read_length = None if length is None else min(length, self._policy.max_value_bytes + 1)
        try:
            data = await asyncio.to_thread(
                _read_bytes_at_most,
                native,
                offset,
                read_length,
                self._policy.max_value_bytes,
            )
        except OSError as exc:
            raise _environment_error_from_os(exc, action="read a binary file") from exc
        if len(data) > self._policy.max_value_bytes:
            raise EnvironmentError("Raw read exceeds value limit.", code="environment_too_large")
        return data

    async def read_bytes_stream(
        self,
        path: str,
        *,
        chunk_size: int = 65_536,
    ) -> AsyncIterator[bytes]:
        if chunk_size < 1:
            raise EnvironmentError("chunk_size must be positive.", code="environment_request_invalid")
        effective_chunk_size = min(chunk_size, self._policy.max_value_bytes)
        native = await asyncio.to_thread(self._resolve_file, path, "Raw read source")
        try:
            file = await asyncio.to_thread(native.open, "rb")
        except OSError as exc:
            raise _environment_error_from_os(exc, action="open a binary file") from exc
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(file.read, effective_chunk_size)
                except OSError as exc:
                    raise _environment_error_from_os(exc, action="read a binary file") from exc
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                await asyncio.to_thread(file.close)
            except OSError:
                pass

    async def write_bytes_stream(
        self,
        path: str,
        stream: AsyncIterable[bytes],
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        async with self._open_writer(
            path,
            mode=mode,
            validate_text_append=False,
        ) as writer:
            async for chunk in stream:
                await writer.write(chunk)
            return await writer.commit()

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        if "\x00" in text:
            raise EnvironmentError("Text contains NUL.", code="environment_unsupported")
        data = text.encode("utf-8")
        if len(data) > self._policy.max_value_bytes:
            raise EnvironmentError("Text write exceeds configured limit.", code="environment_too_large")
        async with self._open_writer(
            path,
            mode=mode,
            validate_text_append=True,
        ) as writer:
            await writer.write(data)
            return await writer.commit()

    async def patch_text(
        self,
        path: str,
        patch: str,
    ) -> FilePatchResult:
        native = await asyncio.to_thread(self._resolve_file, path, "Patch source")
        try:
            data = await asyncio.to_thread(
                _read_bytes_at_most,
                native,
                0,
                self._policy.max_value_bytes + 1,
                self._policy.max_value_bytes + 1,
            )
        except OSError as exc:
            raise _environment_error_from_os(exc, action="read a patch source") from exc
        if len(data) > self._policy.max_value_bytes:
            raise EnvironmentError("Patch source exceeds configured limit.", code="environment_too_large")
        if b"\x00" in data:
            raise EnvironmentError("Patch source contains NUL.", code="environment_unsupported")
        try:
            source = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise EnvironmentError(
                "Patch source is not valid UTF-8.",
                code="environment_unsupported",
            ) from exc
        updated, count = apply_unified_diff(
            source,
            patch,
            max_result_bytes=self._policy.max_value_bytes,
        )
        result = await self.write_text(path, updated, mode="replace")
        return FilePatchResult(
            path=path,
            hunks_applied=count,
            receipt=result.receipt,
        )

    async def stat(self, path: str) -> FileMetadata:
        return await asyncio.to_thread(self._stat_path, path)

    def _stat_path(self, path: str) -> FileMetadata:
        native = self._resolve(path, follow_final=False)
        return self._metadata_for_native(native, path)

    def _metadata_for_native(self, native: Path, logical_path: str) -> FileMetadata:
        try:
            metadata = native.lstat()
        except OSError as exc:
            raise _environment_error_from_os(exc, action="inspect file metadata") from exc
        mode = metadata.st_mode
        if stat_module.S_ISLNK(mode):
            kind: FileKind = "symlink"
        elif stat_module.S_ISREG(mode):
            kind = "file"
        elif stat_module.S_ISDIR(mode):
            kind = "directory"
        else:
            kind = "other"
        return FileMetadata(
            path=logical_path,
            kind=kind,
            size=metadata.st_size if kind == "file" else None,
            writable=not self._read_only and native != self._root,
        )

    async def list(
        self,
        path: str,
        *,
        offset: int = 0,
        max_results: int,
        include_hidden: bool = False,
    ) -> FileEntriesResult:
        if offset < 0 or max_results <= 0:
            raise EnvironmentError("Invalid list range.", code="environment_request_invalid")
        return await asyncio.to_thread(
            self._list_page,
            path,
            offset,
            max_results,
            include_hidden,
        )

    def _list_page(
        self,
        path: str,
        offset: int,
        max_results: int,
        include_hidden: bool,
    ) -> FileEntriesResult:
        native = self._resolve_directory(path, "List path")
        try:
            children = sorted(native.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="enumerate a directory") from exc
        if not include_hidden:
            children = [item for item in children if not item.name.startswith(".")]
        selected = children[offset : offset + max_results]
        entries = tuple(self._metadata_for_native(item, self._logical(item)) for item in selected)
        return FileEntriesResult(
            entries=entries,
            offset=offset,
            has_more=offset + len(selected) < len(children),
        )

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        return await asyncio.to_thread(self._query_page, request)

    def _query_page(self, request: FileQueryRequest) -> FileEntriesResult:
        root = self._resolve_directory(request.root, "Query root")
        selected, has_more = _collect_query_slice(
            root,
            self._root,
            request.pattern,
            request.recursive,
            request.include_hidden,
            request.ignore_mode,
            request.kinds,
            request.offset,
            request.max_results,
        )
        entries = tuple(self._metadata_for_native(item, self._logical(item)) for item in selected)
        return FileEntriesResult(entries=entries, offset=request.offset, has_more=has_more)

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        return await asyncio.to_thread(self._search_page, request)

    def _search_page(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        root = self._resolve_directory(request.root, "Search root")
        try:
            include = PathPattern(request.include, "include")
            regex = content_pattern(request.pattern, request.regex, request.case_sensitive)
        except PatternError as exc:
            raise EnvironmentError(str(exc), code="environment_request_invalid", details=exc.details) from exc
        needle = request.pattern

        matches: list[FileTextMatch] = []
        seen = 0
        files_scanned = 0
        paths = _iter_native_paths(
            root,
            recursive=True,
            include_hidden=request.include_hidden,
            ignore_mode=request.ignore_mode,
            ignore_root=self._root,
        )
        for native in paths:
            relative = native.relative_to(root).as_posix()
            if not include.matches(relative):
                continue
            metadata = _regular_search_file_metadata(native)
            if metadata is None or metadata.st_size > request.max_file_bytes:
                continue
            if request.max_files is not None and files_scanned >= request.max_files:
                raise EnvironmentError(
                    "Text search exceeded the eligible file limit.",
                    code="environment_too_large",
                )
            files_scanned += 1
            per_file_limit = request.max_matches_per_file
            remaining = request.max_matches - len(matches) + 1
            try:
                scanned = search_text_file(
                    native,
                    needle,
                    regex,
                    request.case_sensitive,
                    max(request.offset - seen, 0),
                    remaining,
                    per_file_limit,
                    request.context_lines,
                    request.max_line_length,
                    min(self._policy.max_value_bytes, request.max_file_bytes),
                )
            except OverflowError as exc:
                raise EnvironmentError(str(exc), code="environment_too_large") from exc
            except OSError:
                continue
            if scanned is None:
                continue
            file_matches, file_match_count = scanned
            seen += file_match_count
            matches.extend(
                FileTextMatch(
                    path=self._logical(native),
                    line=line_number,
                    text=text,
                    text_truncated=truncated,
                    context=context,
                    context_start_line=context_start_line,
                )
                for line_number, text, truncated, context, context_start_line in file_matches
            )
            if len(matches) > request.max_matches:
                return FileTextSearchResult(
                    matches=tuple(matches[: request.max_matches]),
                    offset=request.offset,
                    has_more=True,
                )
        return FileTextSearchResult(matches=tuple(matches), offset=request.offset, has_more=False)

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> FileMutationResult:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False, require_exists=False)
        self._require_writable(native)
        try:
            await asyncio.to_thread(native.mkdir, parents=parents, exist_ok=exist_ok)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="create a directory") from exc
        return FileMutationResult(path=path, receipt=self._receipt())

    async def move(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileMutationResult:
        try:
            await asyncio.to_thread(self._move_native, source, destination, replace)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="move a file") from exc
        return FileMutationResult(path=destination, receipt=self._receipt())

    def _move_native(self, source: str, destination: str, replace: bool) -> None:
        source_native = self._resolve(source, follow_final=False)
        destination_native = self._resolve(
            destination,
            follow_final=False,
            require_exists=False,
        )
        self._require_writable(source_native)
        self._require_writable(destination_native)
        if source_native == destination_native:
            return
        if destination_native.exists() and not replace:
            raise EnvironmentError("Move destination exists.", code="environment_conflict")
        if destination_native.is_symlink():
            raise EnvironmentError("Move destination symlink is denied.", code="environment_denied")
        if not replace or not destination_native.exists():
            (os.replace if replace else os.rename)(source_native, destination_native)
            return

        backup = destination_native.with_name(f".{destination_native.name}.a13n-replaced-{uuid4().hex}")
        os.rename(destination_native, backup)
        try:
            os.replace(source_native, destination_native)
        except BaseException:
            if destination_native.exists():
                _remove_native_path(backup)
            else:
                os.replace(backup, destination_native)
            raise
        else:
            _remove_native_path(backup)

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> FileMutationResult:
        try:
            await asyncio.to_thread(self._remove_native, path, recursive)
        except OSError as exc:
            raise _environment_error_from_os(exc, action="remove a file") from exc
        return FileMutationResult(path=path, receipt=self._receipt())

    def _remove_native(self, path: str, recursive: bool) -> None:
        native = self._resolve(path, follow_final=False)
        self._require_writable(native)
        if native.is_symlink() or native.is_file():
            native.unlink()
        elif native.is_dir():
            if recursive:
                shutil.rmtree(native)
            else:
                native.rmdir()
        else:
            raise EnvironmentError("Unsupported file kind.", code="environment_unsupported")

    @asynccontextmanager
    async def _open_writer(
        self,
        path: str,
        *,
        mode: FileWriteMode,
        validate_text_append: bool,
    ) -> AsyncGenerator[_LocalWriter]:
        native = await asyncio.to_thread(self._resolve, path, follow_final=False, require_exists=False)
        self._require_writable(native)
        await asyncio.to_thread(self._validate_writer_destination, native)
        writer = _LocalWriter(
            self,
            path,
            native,
            mode,
            validate_text_append=validate_text_append,
        )
        try:
            await writer.open()
            yield writer
        finally:
            if not writer._committed:
                active_error = sys.exception()
                try:
                    await writer.abort()
                except BaseException as cleanup_error:
                    if active_error is None:
                        raise
                    active_error.add_note(f"Staged file cleanup also failed: {cleanup_error!r}")

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileCopyResult:
        result = await self.write_bytes_stream(
            destination,
            self.read_bytes_stream(source),
            mode="replace" if replace else "create",
        )
        return FileCopyResult(
            path=destination,
            bytes_copied=result.bytes_written,
            receipt=result.receipt,
        )


def _read_bytes_at_most(
    path: Path,
    offset: int,
    length: int | None,
    max_bytes: int,
) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(max_bytes + 1 if length is None else length)


def _read_text_page(
    path: Path,
    line_offset: int,
    line_limit: int,
    max_line_length: int,
) -> tuple[str, int, bool, tuple[int, ...]]:
    def read_line(file: Any) -> tuple[str, bool, bool] | None:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        parts: list[str] = []
        captured = 0
        truncated = False
        saw_bytes = False

        def append_text(value: str) -> None:
            nonlocal captured, truncated
            if "\x00" in value:
                raise EnvironmentError(
                    "Text source contains NUL.",
                    code="environment_unsupported",
                )
            remaining = max(max_line_length - captured, 0)
            if remaining:
                parts.append(value[:remaining])
                captured += min(len(value), remaining)
            if len(value) > remaining:
                truncated = True

        while True:
            raw = file.readline(65_536)
            if not raw:
                if not saw_bytes:
                    return None
                append_text(decoder.decode(b"", final=True))
                return "".join(parts), False, truncated
            saw_bytes = True
            terminated = raw.endswith(b"\n")
            content = raw[:-1] if terminated else raw
            append_text(decoder.decode(content, final=terminated))
            if terminated:
                return "".join(parts), True, truncated

    selected: list[str] = []
    truncated_lines: list[int] = []
    line_index = 0
    with path.open("rb") as file:
        while line_index < line_offset:
            if read_line(file) is None:
                return "", 0, False, ()
            line_index += 1

        while len(selected) < line_limit:
            line = read_line(file)
            if line is None:
                return "".join(selected), len(selected), False, tuple(truncated_lines)
            preview, terminated, truncated = line
            selected.append(preview + ("\n" if terminated else ""))
            if truncated:
                truncated_lines.append(line_index + 1)
            line_index += 1

        has_more = bool(file.read(1))
    return "".join(selected), len(selected), has_more, tuple(truncated_lines)


def _remove_native_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _environment_error_from_os(exc: OSError, *, action: str) -> EnvironmentError:
    if isinstance(exc, FileNotFoundError):
        code = "environment_not_found"
    elif isinstance(exc, FileExistsError):
        code = "environment_conflict"
    elif isinstance(exc, PermissionError):
        code = "environment_denied"
    elif isinstance(exc, NotADirectoryError | IsADirectoryError):
        code = "environment_request_invalid"
    else:
        code = "environment_provider_failure"
    return EnvironmentError(f"Direct Local could not {action}.", code=code)


def _iter_native_paths(
    root: Path,
    *,
    recursive: bool,
    include_hidden: bool,
    ignore_mode: str = "none",
    ignore_root: Path | None = None,
) -> Iterator[Path]:
    """Walk incrementally in deterministic order with optional nested git-ignore pruning."""

    def iterate(
        directory: Path,
        ignore_specs: tuple[tuple[Path, GitIgnoreSpec], ...],
    ) -> Iterator[Path]:
        active_specs = ignore_specs
        if ignore_mode == "git":
            spec = _load_git_ignore(directory / ".gitignore")
            if spec is not None:
                active_specs += ((directory, spec),)
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise EnvironmentError(
                "File query could not enumerate a directory.", code="environment_provider_failure"
            ) from exc
        actions: list[tuple[str, Path, bool]] = []
        for child in children:
            relative = child.relative_to(root)
            if not include_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            try:
                is_directory = child.is_dir() and not child.is_symlink()
            except OSError:
                continue
            if ignore_mode == "git" and _is_git_ignored(child, is_directory, active_specs):
                continue
            actions.append((child.name, child, False))
            if recursive and is_directory:
                actions.append((child.name + "/", child, True))
        # Descending at name + '/' keeps a.txt before a/x.txt without collecting
        # the complete tree or losing the directory entry's own earlier position.
        for _, child, descend in sorted(actions):
            if descend:
                yield from iterate(child, active_specs)
            else:
                yield child

    initial_specs: tuple[tuple[Path, GitIgnoreSpec], ...] = ()
    if ignore_mode == "git" and ignore_root is not None:
        try:
            relative_root = root.relative_to(ignore_root)
        except ValueError:
            relative_root = None
        if relative_root is not None:
            directory = ignore_root
            for part in relative_root.parts:
                if directory == root:
                    break
                spec = _load_git_ignore(directory / ".gitignore")
                if spec is not None:
                    initial_specs += ((directory, spec),)
                directory /= part
    return iterate(root, initial_specs)


def _load_git_ignore(path: Path) -> GitIgnoreSpec | None:
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        with path.open("r", encoding="utf-8", errors="replace") as file:
            return GitIgnoreSpec.from_lines(file)
    except OSError:
        return None


def _is_git_ignored(
    path: Path,
    is_directory: bool,
    specs: tuple[tuple[Path, GitIgnoreSpec], ...],
) -> bool:
    if ".git" in path.parts:
        return True
    ignored = False
    for base, spec in specs:
        try:
            local = path.relative_to(base)
        except ValueError:
            continue
        candidate = local.as_posix() + ("/" if is_directory else "")
        result = spec.check_file(candidate)
        if result.include is not None:
            ignored = result.include
    return ignored


def _collect_query_slice(
    root: Path,
    ignore_root: Path,
    pattern: str,
    recursive: bool,
    include_hidden: bool,
    ignore_mode: str,
    kinds: frozenset[FileKind] | None,
    offset: int,
    limit: int,
) -> tuple[list[Path], bool]:
    """Walk in deterministic path order while retaining only the requested slice and lookahead."""

    try:
        matcher = PathPattern(pattern)
    except PatternError as exc:
        raise EnvironmentError(str(exc), code="environment_request_invalid", details=exc.details) from exc
    selected: list[Path] = []
    matched = 0
    for item in _iter_native_paths(
        root,
        recursive=recursive,
        include_hidden=include_hidden,
        ignore_mode=ignore_mode,
        ignore_root=ignore_root,
    ):
        relative = item.relative_to(root).as_posix()
        if not matcher.matches(relative):
            continue
        kind = _native_kind(item)
        if kinds is not None and kind not in kinds:
            continue
        if matched < offset:
            matched += 1
            continue
        if len(selected) == limit:
            return selected, True
        selected.append(item)
        matched += 1
    return selected, False


def _native_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "other"


def _regular_search_file_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _environment_error_from_os(exc, action="inspect a search candidate") from exc
    return metadata if stat_module.S_ISREG(metadata.st_mode) else None
