from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Coroutine
from types import TracebackType
from typing import Any, Self

from a13n_envd_client._transfer_window import TransferWindow
from a13n_envd_client.eip.v1 import (
    EIP_DATA_FRAME_HEADER_BYTES,
    EIP_TRANSFER_WINDOW_CHUNKS,
    ContentDigest,
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    EIPCallContext,
    EIPClient,
    EIPPath,
    FileByteRange,
    FileReadCompletion,
    FileReaderCloseParams,
    FileReaderOpenParams,
    FileReaderOpenResult,
    FileWriteMode,
    FileWriterAbortParams,
    FileWriterAbortResult,
    FileWriterAbortStatus,
    FileWriterCommitParams,
    FileWriterCommitResult,
    FileWriterOpenParams,
    FileWriterOpenResult,
)
from a13n_envd_client.errors import (
    EIPClientError,
    EIPProtocolError,
    EIPSessionStateError,
    EIPTransferError,
)
from a13n_envd_client.requester import SessionRequester, TransferChannel


class EIPFileReader:
    """High-level bounded reader that owns attachment, integrity, and typed close."""

    def __init__(
        self,
        requester: SessionRequester,
        client: EIPClient,
        path: EIPPath,
        *,
        byte_range: FileByteRange | None,
        transfer_timeout_ms: int | None,
    ) -> None:
        self._requester = requester
        self._client = client
        self._params = FileReaderOpenParams(
            context=_new_context(),
            path=path,
            byte_range=byte_range,
            transfer_timeout_ms=transfer_timeout_ms,
        )
        self._opened: FileReaderOpenResult | None = None
        self._channel: TransferChannel | None = None
        self._hasher = hashlib.sha256()
        self._received_bytes = 0
        self._credited_bytes = 0
        self._max_bytes: int | None = None
        self._completion: FileReadCompletion | None = None
        self._entered = False
        self._finalized = False

    @property
    def open_context(self) -> EIPCallContext:
        return self._params.context

    @property
    def completion(self) -> FileReadCompletion:
        if self._completion is None:
            raise EIPSessionStateError("reader has not completed successfully")
        return self._completion

    @property
    def opened(self) -> FileReaderOpenResult:
        if self._opened is None:
            raise EIPSessionStateError("reader has not been opened")
        return self._opened

    async def __aenter__(self) -> Self:
        if self._entered:
            raise EIPSessionStateError("reader context cannot be entered more than once")
        self._entered = True
        try:
            self._opened = await self._client.file_open_reader(self._params)
            byte_range = self._params.byte_range
            if byte_range is not None and byte_range.length is not None:
                self._max_bytes = byte_range.length
            else:
                size = self._opened.info.size_bytes
                if size is None:
                    raise EIPProtocolError("reader open result omitted the regular-file size")
                offset = 0 if byte_range is None else byte_range.offset
                self._max_bytes = max(size - offset, 0)
            handle = self._opened.reader.root
            self._channel = self._requester.register_transfer(
                handle,
                direction="read",
                inbound_frames=EIP_TRANSFER_WINDOW_CHUNKS + 1,
            )
            await self._requester.send_data_frame(
                self._channel,
                DataFrame(kind=DataFrameKind.ATTACH, session_id=self._requester.session_id, handle=handle),
            )
            attached = await self._channel.receive()
            _expect_frame(attached, DataFrameKind.ATTACHED, offset=0)
            return self
        except BaseException:
            await _ignore_cleanup_failure(self._abandon())
            raise

    def __aiter__(self) -> Self:
        if not self._entered:
            raise EIPSessionStateError("reader context has not been entered")
        return self

    async def __anext__(self) -> bytes:
        if not self._entered:
            raise EIPSessionStateError("reader context has not been entered")
        if self._finalized:
            raise StopAsyncIteration
        channel = self._require_channel()
        # Returning for the next chunk means the caller consumed the previous one.
        if self._received_bytes != self._credited_bytes:
            await self._requester.send_data_frame(
                channel,
                DataFrame(
                    kind=DataFrameKind.CREDIT,
                    session_id=channel.session_id,
                    handle=channel.handle,
                    offset=self._received_bytes,
                ),
            )
            self._credited_bytes = self._received_bytes
        frame = await channel.receive()
        if frame.kind is DataFrameKind.CHUNK:
            if frame.offset != self._received_bytes:
                await self._reset_for_protocol(frame.offset)
                raise EIPProtocolError("reader data frame offset is not contiguous")
            received = self._received_bytes + len(frame.payload)
            if self._max_bytes is not None and received > self._max_bytes:
                await self._reset_for_protocol(frame.offset)
                raise EIPProtocolError("reader produced more bytes than the requested maximum")
            self._hasher.update(frame.payload)
            self._received_bytes = received
            return frame.payload
        if frame.kind is DataFrameKind.END:
            if frame.offset != self._received_bytes:
                await self._reset_for_protocol(frame.offset)
                raise EIPProtocolError("reader terminal offset does not match consumed bytes")
            result = await self._client.file_close_reader(
                FileReaderCloseParams(
                    context=_new_context(),
                    reader=self.opened.reader,
                )
            )
            try:
                self._verify_completion(result.completion)
            except EIPProtocolError as error:
                self._finalize_channel()
                await _ignore_cleanup_failure(self._requester.close_for_protocol_error(error))
                raise
            self._completion = result.completion
            self._finalize_channel()
            raise StopAsyncIteration
        if frame.kind is DataFrameKind.RESET:
            raise _peer_reset("reader", frame)
        await self._reset_for_protocol(frame.offset)
        raise EIPProtocolError(f"unexpected {frame.kind.name} frame on a reader transfer")

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._finalized:
            return
        cleanup = asyncio.create_task(self._abandon(), name="eip-reader-abandon")
        if exception_type is not None:
            await _ignore_cleanup_failure_task(cleanup)
            return
        await _await_shared_cleanup(cleanup)

    async def _abandon(self) -> None:
        if self._finalized:
            return
        first_error: BaseException | None = None
        channel = self._channel
        channel_retired = False
        if channel is not None and not channel.peer_reset_received:
            try:
                await self._requester.reset_transfer(
                    channel,
                    DataFrame(
                        kind=DataFrameKind.RESET,
                        session_id=channel.session_id,
                        handle=channel.handle,
                        offset=self._received_bytes,
                        reset_status=DataResetStatus.CANCELLED,
                    ),
                )
                await self._requester.wait_transfer_retired(channel)
                channel_retired = True
            except BaseException as error:
                channel_retired = self._requester.transfer_is_retired(channel)
                first_error = error
        if channel_retired:
            self._channel = None
            self._finalized = True
        elif channel is not None and channel.peer_reset_received:
            self._finalize_channel()
        else:
            self._finalize_channel(retire=True)
        if first_error is not None:
            raise first_error

    async def _reset_for_protocol(self, offset: int) -> None:
        channel = self._require_channel()
        try:
            await self._requester.reset_transfer(
                channel,
                DataFrame(
                    kind=DataFrameKind.RESET,
                    session_id=channel.session_id,
                    handle=channel.handle,
                    offset=offset,
                    reset_status=DataResetStatus.PROTOCOL,
                ),
            )
        except EIPClientError:
            pass

    def _verify_completion(self, completion: FileReadCompletion) -> None:
        if completion.produced_bytes != self._received_bytes:
            raise EIPProtocolError("reader byte count differs from envd completion evidence")
        if self._max_bytes is None or completion.produced_bytes > self._max_bytes:
            raise EIPProtocolError("reader completion exceeds the negotiated maximum")
        digest = completion.digest
        if digest.algorithm != "sha256" or digest.value != self._hasher.hexdigest():
            raise EIPProtocolError("reader digest differs from envd completion evidence")

    def _require_channel(self) -> TransferChannel:
        if self._channel is None:
            raise EIPSessionStateError("reader transfer is not attached")
        return self._channel

    def _finalize_channel(self, *, retire: bool = False) -> None:
        channel = self._channel
        if channel is not None:
            if retire:
                self._requester.retire_transfer(channel)
            else:
                self._requester.unregister_transfer(channel)
            self._channel = None
        self._finalized = True


class EIPFileWriter:
    """High-level staged writer that hides framed upload and abort ownership."""

    def __init__(
        self,
        requester: SessionRequester,
        client: EIPClient,
        path: EIPPath,
        mode: FileWriteMode,
        *,
        executable: bool | None,
        transfer_timeout_ms: int | None,
        max_transfer_frame_bytes: int,
    ) -> None:
        self._requester = requester
        self._client = client
        self._params = FileWriterOpenParams(
            context=_new_context(),
            path=path,
            mode=mode,
            executable=executable,
            transfer_timeout_ms=transfer_timeout_ms,
        )
        self._max_transfer_frame_bytes = max_transfer_frame_bytes
        self._opened: FileWriterOpenResult | None = None
        self._channel: TransferChannel | None = None
        self._hasher = hashlib.sha256()
        self._transferred_bytes = 0
        self._window = TransferWindow()
        self._result: FileWriterCommitResult | None = None
        self._commit_context = _new_context()
        self._entered = False
        self._sealed = False
        self._finalized = False
        self._lock = asyncio.Lock()

    @property
    def open_context(self) -> EIPCallContext:
        return self._params.context

    @property
    def commit_context(self) -> EIPCallContext:
        return self._commit_context

    @property
    def result(self) -> FileWriterCommitResult:
        if self._result is None:
            raise EIPSessionStateError("writer has not committed successfully")
        return self._result

    @property
    def opened(self) -> FileWriterOpenResult:
        if self._opened is None:
            raise EIPSessionStateError("writer has not been opened")
        return self._opened

    @property
    def transferred_bytes(self) -> int:
        return self._transferred_bytes

    async def __aenter__(self) -> Self:
        if self._entered:
            raise EIPSessionStateError("writer context cannot be entered more than once")
        self._entered = True
        try:
            self._opened = await self._client.file_open_writer(self._params)
            handle = self._opened.writer.root
            self._channel = self._requester.register_transfer(
                handle,
                direction="write",
                inbound_frames=EIP_TRANSFER_WINDOW_CHUNKS + 1,
            )
            await self._requester.send_data_frame(
                self._channel,
                DataFrame(kind=DataFrameKind.ATTACH, session_id=self._requester.session_id, handle=handle),
            )
            attached = await self._channel.receive()
            _expect_frame(attached, DataFrameKind.ATTACHED, offset=0)
            return self
        except BaseException:
            await _ignore_cleanup_failure(self._abort())
            raise

    async def write(self, chunk: bytes | bytearray | memoryview) -> None:
        if isinstance(chunk, bytes):
            payload = chunk
        elif isinstance(chunk, (bytearray, memoryview)):
            payload = bytes(chunk)
        else:
            raise TypeError("writer chunks must be bytes-like")
        async with self._lock:
            self._ensure_writable()
            if not payload:
                return
            opened = self.opened
            next_total = self._transferred_bytes + len(payload)
            if next_total > opened.max_transfer_bytes:
                raise EIPTransferError("writer payload exceeds its negotiated transfer byte limit")
            channel = self._require_channel()
            payload_limit = (
                self._max_transfer_frame_bytes
                - EIP_DATA_FRAME_HEADER_BYTES
                - len(channel.session_id.encode("utf-8"))
                - len(channel.handle.encode("utf-8"))
            )
            if payload_limit < 1:
                raise EIPProtocolError("negotiated data frame limit cannot carry writer payload")
            for start in range(0, len(payload), payload_limit):
                part = payload[start : start + payload_limit]
                if self._window.full:
                    await self._receive_credit(channel)
                offset = self._window.sent(len(part))
                await self._requester.send_data_frame(
                    channel,
                    DataFrame(
                        kind=DataFrameKind.CHUNK,
                        session_id=channel.session_id,
                        handle=channel.handle,
                        offset=offset,
                        payload=part,
                    ),
                )
                self._hasher.update(part)
                self._transferred_bytes += len(part)

    async def _receive_credit(self, channel: TransferChannel) -> None:
        frame = await channel.receive()
        if frame.kind is DataFrameKind.RESET:
            raise _peer_reset("writer", frame)
        if frame.kind is not DataFrameKind.CREDIT:
            raise EIPProtocolError("writer expected chunk credit")
        self._window.credit(frame.offset)

    async def commit(self) -> FileWriterCommitResult:
        async with self._lock:
            self._ensure_writable()
            channel = self._require_channel()
            while self._window.pending:
                await self._receive_credit(channel)
            await self._requester.send_data_frame(
                channel,
                DataFrame(
                    kind=DataFrameKind.END,
                    session_id=channel.session_id,
                    handle=channel.handle,
                    offset=self._transferred_bytes,
                ),
            )
            self._sealed = True
            terminal = await channel.receive()
            if terminal.kind is DataFrameKind.RESET:
                raise _peer_reset("writer", terminal)
            _expect_frame(terminal, DataFrameKind.END_ACK, offset=self._transferred_bytes)
            digest = ContentDigest(algorithm="sha256", value=self._hasher.hexdigest())
            result = await self._client.file_commit_writer(
                FileWriterCommitParams(
                    context=self._commit_context,
                    writer=self.opened.writer,
                    transferred_bytes=self._transferred_bytes,
                    transfer_digest=digest,
                )
            )
            self._result = result
            self._finalize_channel()
            if result.transferred_bytes != self._transferred_bytes or result.transfer_digest != digest:
                raise EIPProtocolError("writer commit evidence differs from the uploaded bytes")
            return result

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._finalized:
            return
        cleanup = asyncio.create_task(self._abort(), name="eip-writer-abort")
        if exception_type is not None:
            await _ignore_cleanup_failure_task(cleanup)
            return
        await _await_shared_cleanup(cleanup)

    async def _abort(self) -> FileWriterAbortResult | None:
        if self._finalized:
            return None
        first_error: BaseException | None = None
        channel = self._channel
        channel_retired = False
        if channel is not None and not channel.peer_reset_received:
            try:
                await self._requester.reset_transfer(
                    channel,
                    DataFrame(
                        kind=DataFrameKind.RESET,
                        session_id=channel.session_id,
                        handle=channel.handle,
                        offset=self._transferred_bytes,
                        reset_status=DataResetStatus.CANCELLED,
                    ),
                )
                channel_retired = True
            except BaseException as error:
                channel_retired = self._requester.transfer_is_retired(channel)
                first_error = error
        result: FileWriterAbortResult | None = None
        if self._opened is not None:
            try:
                result = await self._client.file_abort_writer(
                    FileWriterAbortParams(
                        context=_new_context(),
                        writer=self._opened.writer,
                    )
                )
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if channel_retired:
            self._channel = None
            self._finalized = True
        elif channel is not None and channel.peer_reset_received:
            self._finalize_channel()
        else:
            self._finalize_channel(retire=True)
        if first_error is not None:
            raise first_error
        if result is not None and result.status in {
            FileWriterAbortStatus.COMMIT_IN_PROGRESS,
            FileWriterAbortStatus.ALREADY_COMMITTED,
        }:
            if channel_retired and channel is not None:
                self._requester.complete_retired_transfer(channel.handle)
            raise EIPTransferError(f"writer abort could not prove rollback: {result.status.value}")
        return result

    def _ensure_writable(self) -> None:
        if not self._entered:
            raise EIPSessionStateError("writer context has not been entered")
        if self._finalized:
            raise EIPSessionStateError("writer is already finalized")
        if self._sealed:
            raise EIPSessionStateError("writer data stream is already sealed")

    def _require_channel(self) -> TransferChannel:
        if self._channel is None:
            raise EIPSessionStateError("writer transfer is not attached")
        return self._channel

    def _finalize_channel(self, *, retire: bool = False) -> None:
        channel = self._channel
        if channel is not None:
            if retire:
                self._requester.retire_transfer(channel)
            else:
                self._requester.unregister_transfer(channel)
            self._channel = None
        self._finalized = True


def _new_context() -> EIPCallContext:
    return EIPCallContext(operation_id=f"op-{secrets.token_urlsafe(9)}")


def _expect_frame(frame: DataFrame, kind: DataFrameKind, *, offset: int) -> None:
    if frame.kind is DataFrameKind.RESET:
        raise _peer_reset("transfer", frame)
    if frame.kind is not kind or frame.offset != offset:
        raise EIPProtocolError(
            f"expected {kind.name} at offset {offset}, got {frame.kind.name} at offset {frame.offset}"
        )


def _peer_reset(direction: str, frame: DataFrame) -> EIPTransferError:
    return EIPTransferError(
        f"{direction} transfer was reset by the EIP peer",
        status=frame.reset_status,
        offset=frame.offset,
    )


async def _ignore_cleanup_failure(awaitable: Coroutine[Any, Any, object]) -> None:
    task = asyncio.create_task(awaitable)
    await _ignore_cleanup_failure_task(task)


async def _ignore_cleanup_failure_task(task: asyncio.Task[object]) -> None:
    try:
        await _await_shared_cleanup(task)
    except BaseException:
        pass


async def _await_shared_cleanup[T](task: asyncio.Task[T]) -> T:
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            raise asyncio.CancelledError
        return result
