from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from pydantic import ValidationError

from ..files import (
    FileCopyResult,
    FileEntriesResult,
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
from ..models import EnvironmentError
from ._common import convert_receipt, invoke, new_context, raise_converted, session_client


class EIPFileOperator:
    def __init__(
        self,
        *,
        session: EIPSession,
        environment_id: str,
        mount_id: str,
        generation: str,
    ) -> None:
        self._session = session
        self._environment_id = environment_id
        self._mount_id = mount_id
        self._generation = generation
        self._mounts = tuple(sorted(session.descriptor.mounts, key=lambda mount: len(mount.logical_root), reverse=True))
        self._mount_by_id = {mount.mount_id: mount for mount in self._mounts}

    @property
    def _client(self) -> eip.EIPClient:
        return session_client(self._session)

    def to_eip_path(self, path: str) -> eip.EIPPath:
        if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
            raise EnvironmentError("Environment path is invalid", code="environment_request_invalid")
        for mount in self._mounts:
            root = mount.logical_root.rstrip("/") or "/"
            if root == "/":
                relative = path
            elif path == root:
                relative = "/"
            elif path.startswith(root + "/"):
                relative = path[len(root) :]
            else:
                continue
            try:
                return eip.EIPPath(mount_id=mount.mount_id, path=relative)
            except ValidationError:
                raise EnvironmentError("Environment path is invalid", code="environment_request_invalid") from None
        raise EnvironmentError("Environment path is outside advertised mounts", code="environment_not_found")

    def from_eip_path(self, path: eip.EIPPath) -> str:
        mount = self._mount_by_id.get(path.mount_id)
        if mount is None:
            raise EnvironmentError("EIP returned an unknown mount", code="environment_provider_failure")
        root = mount.logical_root.rstrip("/") or "/"
        return path.path if root == "/" else root + ("" if path.path == "/" else path.path)

    async def read_text(
        self,
        path: str,
        *,
        line_offset: int = 0,
        line_limit: int = 200,
        max_line_length: int = 2_000,
    ) -> FileTextResult:
        result = await invoke(
            self._client.file_read_text(
                eip.FileReadTextParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    line_offset=line_offset,
                    line_limit=line_limit,
                    max_line_length=max_line_length,
                )
            )
        )
        return FileTextResult(
            path=self.from_eip_path(result.info.path),
            text=result.text,
            line_offset=result.line_offset,
            lines_read=result.lines_read,
            has_more=result.has_more,
            truncated_lines=result.truncated_lines,
        )

    async def read_bytes(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        try:
            self._require_transfer_method("file.open_reader")
            reader = self._session.open_reader(
                self.to_eip_path(path),
                byte_range=eip.FileByteRange(offset=offset, length=length),
            )
            async with reader:
                return b"".join([chunk async for chunk in reader])
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise_converted(error)

    async def _read_bytes_stream(self, path: str, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        try:
            self._require_transfer_method("file.open_reader")
            reader = self._session.open_reader(self.to_eip_path(path))
            async with reader:
                async for chunk in reader:
                    yield chunk
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise_converted(error)

    def read_bytes_stream(self, path: str, *, chunk_size: int = 65_536) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        return self._read_bytes_stream(path, chunk_size)

    async def write_bytes_stream(
        self,
        path: str,
        stream: AsyncIterable[bytes],
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        try:
            self._require_transfer_method("file.open_writer")
            writer = self._session.open_writer(self.to_eip_path(path), mode=eip.FileWriteMode(mode))
            async with writer:
                async for chunk in stream:
                    await writer.write(chunk)
                result = await writer.commit()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise_converted(error)
        return FileWriteResult(
            path=self.from_eip_path(result.info.path),
            bytes_written=result.transferred_bytes,
            receipt=self._receipt(result.receipt),
        )

    def _require_transfer_method(self, method: str) -> None:
        if method not in self._session.descriptor.available_methods:
            raise EnvironmentError("EIP file transfer is not supported", code="environment_unsupported")

    async def write_text(
        self,
        path: str,
        text: str,
        *,
        mode: FileWriteMode,
    ) -> FileWriteResult:
        result = await invoke(
            self._client.file_write_text(
                eip.FileWriteTextParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    mode=eip.FileWriteMode(mode),
                    text=text,
                )
            )
        )
        return FileWriteResult(
            path=self.from_eip_path(result.info.path),
            bytes_written=result.bytes_written,
            receipt=self._receipt(result.receipt),
        )

    async def patch_text(self, path: str, patch: str) -> FilePatchResult:
        result = await invoke(
            self._client.file_patch_text(
                eip.FilePatchTextParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    patch_format="unified_diff",
                    patch=patch,
                )
            )
        )
        return FilePatchResult(
            path=self.from_eip_path(result.info.path),
            hunks_applied=result.hunks_applied,
            receipt=self._receipt(result.receipt),
        )

    async def stat(self, path: str) -> FileMetadata:
        result = await invoke(
            self._client.file_stat(
                eip.FileStatParams(context=new_context(), path=self.to_eip_path(path), follow_symlinks=False)
            )
        )
        return self._metadata(result.info)

    async def list(
        self,
        path: str,
        *,
        offset: int = 0,
        max_results: int,
        include_hidden: bool = False,
    ) -> FileEntriesResult:
        result = await invoke(
            self._client.file_list(
                eip.FileListParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    offset=offset,
                    max_results=max_results,
                    include_hidden=include_hidden,
                )
            )
        )
        return FileEntriesResult(
            entries=tuple(self._metadata(entry.info) for entry in result.entries),
            offset=result.offset,
            has_more=result.has_more,
        )

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        result = await invoke(
            self._client.file_find(
                eip.FileFindParams(
                    context=new_context(),
                    root=self.to_eip_path(request.root),
                    pattern=request.pattern,
                    recursive=request.recursive,
                    include_hidden=request.include_hidden,
                    kinds=() if request.kinds is None else tuple(eip.FileKind(kind) for kind in sorted(request.kinds)),
                    respect_git_ignore=request.ignore_mode == "git",
                    offset=request.offset,
                    max_results=request.max_results,
                )
            )
        )
        return FileEntriesResult(
            entries=tuple(self._metadata(entry.info) for entry in result.entries),
            offset=result.offset,
            has_more=result.has_more,
        )

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        result = await invoke(
            self._client.file_search(
                eip.FileSearchParams(
                    context=new_context(),
                    root=self.to_eip_path(request.root),
                    query=request.pattern,
                    mode=eip.SearchMode.REGEX if request.regex else eip.SearchMode.LITERAL,
                    case_sensitive=request.case_sensitive,
                    include_hidden=request.include_hidden,
                    offset=request.offset,
                    max_results=request.max_matches,
                    max_line_length=request.max_line_length,
                    include_pattern=request.include,
                    respect_git_ignore=request.ignore_mode == "git",
                    context_lines=request.context_lines,
                    max_matches_per_file=request.max_matches_per_file,
                    max_files=request.max_files,
                    max_file_bytes=request.max_file_bytes,
                )
            )
        )
        return FileTextSearchResult(
            matches=tuple(
                FileTextMatch(
                    path=self.from_eip_path(match.path),
                    line=match.line_number,
                    text=match.preview,
                    text_truncated=match.preview_truncated,
                    context=match.context,
                    context_start_line=match.context_start_line,
                )
                for match in result.matches
            ),
            offset=result.offset,
            has_more=result.has_more,
        )

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> FileMutationResult:
        result = await invoke(
            self._client.file_mkdir(
                eip.FileMkdirParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    parents=parents,
                    exist_ok=exist_ok,
                )
            )
        )
        return FileMutationResult(path=self.from_eip_path(result.info.path), receipt=self._receipt(result.receipt))

    async def move(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileMutationResult:
        result = await invoke(
            self._client.file_move(
                eip.FileMoveParams(
                    context=new_context(),
                    source=self.to_eip_path(source),
                    destination=self.to_eip_path(destination),
                    replace=replace,
                )
            )
        )
        return FileMutationResult(
            path=self.from_eip_path(result.destination.path),
            receipt=self._receipt(result.receipt),
        )

    async def remove(self, path: str, *, recursive: bool = False) -> FileMutationResult:
        target = await self.stat(path)
        result = await invoke(
            self._client.file_remove(
                eip.FileRemoveParams(
                    context=new_context(),
                    path=self.to_eip_path(path),
                    expected_kind=eip.FileKind(target.kind),
                    recursive=recursive,
                    max_entries=2**64 - 1,
                )
            )
        )
        return FileMutationResult(path=path, receipt=self._receipt(result.receipt))

    async def copy(
        self,
        source: str,
        destination: str,
        *,
        replace: bool = False,
    ) -> FileCopyResult:
        result = await invoke(
            self._client.file_copy(
                eip.FileCopyParams(
                    context=new_context(),
                    source=self.to_eip_path(source),
                    destination=self.to_eip_path(destination),
                    replace=replace,
                )
            )
        )
        return FileCopyResult(
            path=self.from_eip_path(result.destination.path),
            bytes_copied=result.bytes_copied,
            receipt=self._receipt(result.receipt),
        )

    def _metadata(self, info: eip.FileInfo) -> FileMetadata:
        mount = self._mount_by_id.get(info.path.mount_id)
        if mount is None:
            raise EnvironmentError("EIP returned an unknown mount", code="environment_provider_failure")
        return FileMetadata(
            path=self.from_eip_path(info.path),
            kind=info.kind.value,
            size=info.size_bytes,
            writable=mount.writable,
        )

    def _receipt(self, receipt: eip.OperationReceipt):
        return convert_receipt(
            receipt,
            environment_id=self._session.descriptor.environment_id,
            mount_id=self._mount_id,
            generation=self._generation,
        )
