"""Worker file facet using one operation and one terminal per byte transfer."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from contextlib import aclosing

from a13n_harness.providers.environment.files import (
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
from pydantic import BaseModel

from a13n_service.ids import new_object_id

from . import relay_files as wire
from .relay_client import RelayUseClient
from .relay_protocol import RelayChunk, RelayCredit, RelayFinish, TransferPosition
from .relay_transfers import ReadBytes, WriteBytes


class RelayFileOperations:
    def __init__(self, client: RelayUseClient) -> None:
        self._client = client

    async def _call[T: BaseModel](self, request: wire.FileRequest, result: type[T]) -> T:
        value = await self._client.call(request.operation, request.model_dump(mode="json", exclude={"operation"}))
        return result.model_validate(value)

    async def read_text(
        self, path: str, *, line_offset: int = 0, line_limit: int = 200, max_line_length: int = 2_000
    ) -> FileTextResult:
        return await self._call(
            wire.ReadText(path=path, line_offset=line_offset, line_limit=line_limit, max_line_length=max_line_length),
            FileTextResult,
        )

    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
        return b"".join([data async for data in self._download(path, offset=offset, length=length)])

    async def read_bytes_stream(self, path: str, *, chunk_size: int = 65_536) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("File chunk size must be positive")
        async with aclosing(self._download(path)) as stream:
            async for data in stream:
                for offset in range(0, len(data), chunk_size):
                    yield data[offset : offset + chunk_size]

    async def _download(self, path: str, *, offset: int = 0, length: int | None = None) -> AsyncGenerator[bytes]:
        request = ReadBytes(transfer_id=new_object_id("etr"), path=path, offset=offset, length=length)
        async with self._client.request(
            request.operation, request.model_dump(mode="json", exclude={"operation"}), streaming="download"
        ) as pending:
            while (chunk := await pending.next_chunk()) is not None:
                # Credit is issued once the frame leaves the bounded mailbox.
                # At most one additional frame is held by this generator.
                await self._client.send_input(
                    pending,
                    RelayCredit(
                        request_id=pending.request.request_id,
                        use=pending.request.use,
                        transfer=TransferPosition(
                            transfer_id=chunk.position.transfer_id,
                            sequence=chunk.position.sequence + 1,
                            offset=chunk.position.offset + len(chunk.data),
                        ),
                    ),
                )
                yield chunk.data
            await pending.result()

    async def write_bytes_stream(
        self, path: str, stream: AsyncIterable[bytes], *, mode: FileWriteMode
    ) -> FileWriteResult:
        request = WriteBytes(transfer_id=new_object_id("etr"), path=path, mode=mode)
        async with self._client.request(
            request.operation, request.model_dump(mode="json", exclude={"operation"}), streaming="upload"
        ) as pending:
            async for data in stream:
                for offset in range(0, len(data), self._client.limits.chunk_bytes):
                    chunk = data[offset : offset + self._client.limits.chunk_bytes]
                    window = await pending.upload_slot()
                    await self._client.send_input(
                        pending,
                        RelayChunk(
                            request_id=pending.request.request_id,
                            use=pending.request.use,
                            transfer=window.sent(len(chunk)),
                            data=base64.b64encode(chunk).decode(),
                        ),
                    )
            await self._client.send_input(
                pending,
                RelayFinish(
                    request_id=pending.request.request_id,
                    use=pending.request.use,
                    transfer=pending.finish_upload(),
                ),
            )
            return FileWriteResult.model_validate((await pending.result()).result)

    async def write_text(self, path: str, text: str, *, mode: FileWriteMode) -> FileWriteResult:
        return await self._call(wire.WriteText(path=path, text=text, mode=mode), FileWriteResult)

    async def patch_text(self, path: str, patch: str) -> FilePatchResult:
        return await self._call(wire.PatchText(path=path, patch=patch), FilePatchResult)

    async def stat(self, path: str) -> FileMetadata:
        return await self._call(wire.Stat(path=path), FileMetadata)

    async def list(
        self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False
    ) -> FileEntriesResult:
        return await self._call(
            wire.ListFiles(path=path, offset=offset, max_results=max_results, include_hidden=include_hidden),
            FileEntriesResult,
        )

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        return await self._call(wire.Query(request=request), FileEntriesResult)

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        return await self._call(wire.SearchText(request=request), FileTextSearchResult)

    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> FileMutationResult:
        return await self._call(wire.Mkdir(path=path, parents=parents, exist_ok=exist_ok), FileMutationResult)

    async def move(self, source: str, destination: str, *, replace: bool = False) -> FileMutationResult:
        return await self._call(wire.Move(source=source, destination=destination, replace=replace), FileMutationResult)

    async def remove(self, path: str, *, recursive: bool = False) -> FileMutationResult:
        return await self._call(wire.Remove(path=path, recursive=recursive), FileMutationResult)

    async def copy(self, source: str, destination: str, *, replace: bool = False) -> FileCopyResult:
        return await self._call(wire.Copy(source=source, destination=destination, replace=replace), FileCopyResult)
