from __future__ import annotations

import asyncio
from enum import Enum

from a13n_envd_client.eip.v1 import (
    DataFrame,
    DataFrameCodecError,
    decode_data_frame,
    encode_data_frame,
)
from a13n_envd_client.errors import EIPProtocolError, EIPTransportClosedError, EIPTransportError
from a13n_envd_client.transport import ControlFrame, EIPTransportFrame

_MAX_HEADER_BYTES = 8 * 1024
_MAX_HEADER_LINE_BYTES = 4 * 1024
_MAX_HEADER_COUNT = 32
_CLOSE_GRACE_SECONDS = 1.0
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_DATA_CONTENT_TYPE = "application/vnd.a13n.eip-data"
_SECURITY_SENSITIVE_HEADERS = {"authorization", "content-encoding", "eip-session", "transfer-encoding"}


class _FrameContentType(Enum):
    JSON = "json"
    DATA = "data"


class StdioTransport:
    """LSP-style EIP framing over trusted parent-supplied asyncio pipes."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        process: asyncio.subprocess.Process | None = None,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
        max_transfer_frame_bytes: int = 1024 * 1024,
    ) -> None:
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        _validate_limit("max_transfer_frame_bytes", max_transfer_frame_bytes)
        self._reader = reader
        self._writer = writer
        self._process = process
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._max_transfer_frame_bytes = max_transfer_frame_bytes
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    def from_process(
        cls,
        process: asyncio.subprocess.Process,
        *,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
        max_transfer_frame_bytes: int = 1024 * 1024,
    ) -> StdioTransport:
        if process.stdin is None or process.stdout is None:
            raise ValueError("process must have asyncio stdin and stdout pipes")
        return cls(
            process.stdout,
            process.stdin,
            process=process,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            max_transfer_frame_bytes=max_transfer_frame_bytes,
        )

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None:
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        _validate_limit("max_transfer_frame_bytes", max_transfer_frame_bytes)
        self._max_request_bytes = min(self._max_request_bytes, max_request_bytes)
        self._max_response_bytes = min(self._max_response_bytes, max_response_bytes)
        self._max_transfer_frame_bytes = min(self._max_transfer_frame_bytes, max_transfer_frame_bytes)

    async def send(self, frame: EIPTransportFrame) -> None:
        if self._closed:
            raise EIPTransportClosedError("stdio transport is closed")
        content_type, payload, maximum = self._encode_outbound(frame)
        if len(payload) > maximum:
            raise EIPTransportError("EIP frame exceeds its negotiated byte limit")

        task = asyncio.create_task(self._send_frame(content_type, payload))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Preserve framing if the write has already completed. If drain is still
            # blocked, make the carrier terminal rather than leave a partial frame.
            if not task.done():
                self._closed = True
                self._writer.close()
                done, _ = await asyncio.wait({task}, timeout=_CLOSE_GRACE_SECONDS)
                if task not in done:
                    task.cancel()
                    task.add_done_callback(_consume_task_exception)
                    raise
            try:
                task.result()
            except Exception as error:
                # A concrete transport failure takes precedence over cancellation so
                # the requester can terminate every pending correlation.
                raise error
            raise

    async def receive(self) -> EIPTransportFrame:
        if self._closed:
            raise EIPTransportClosedError("stdio transport is closed")
        async with self._read_lock:
            try:
                headers = await self._read_headers()
                content_length, content_type = _parse_headers(
                    headers,
                    self._max_response_bytes,
                    self._max_transfer_frame_bytes,
                )
                payload = await self._reader.readexactly(content_length)
                if content_type is _FrameContentType.JSON:
                    return ControlFrame(payload)
                try:
                    return decode_data_frame(payload, max_frame_bytes=self._max_transfer_frame_bytes)
                except DataFrameCodecError as error:
                    raise EIPProtocolError("invalid EIP stdio data frame") from error
            except asyncio.IncompleteReadError as error:
                returncode = self._process.returncode if self._process is not None else None
                detail = "stdio response stream reached EOF"
                if returncode is not None:
                    detail = f"{detail} (process exit code {returncode})"
                raise EIPTransportClosedError(detail) from error
            except (ConnectionError, OSError) as error:
                raise EIPTransportError("failed to read an EIP stdio response") from error

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            # Closing before waiting for the write lock wakes a drain blocked by
            # backpressure and makes any partial frame terminal for the carrier.
            self._writer.close()
            self._close_task = asyncio.create_task(self._finish_close(), name="eip-stdio-close")
        close_task = self._close_task
        await _await_shared_close(close_task)

    async def _finish_close(self) -> None:
        try:
            async with asyncio.timeout(_CLOSE_GRACE_SECONDS):
                async with self._write_lock:
                    try:
                        await self._writer.wait_closed()
                    except (BrokenPipeError, ConnectionError, OSError):
                        pass
        except TimeoutError:
            pass

    async def _send_frame(self, content_type: str, payload: bytes) -> None:
        async with self._write_lock:
            if self._closed:
                raise EIPTransportClosedError("stdio transport is closed")
            header = (f"Content-Length: {len(payload)}\r\nContent-Type: {content_type}\r\n\r\n").encode("ascii")
            try:
                self._writer.write(header)
                self._writer.write(payload)
                await self._writer.drain()
            except (BrokenPipeError, ConnectionError, OSError) as error:
                raise EIPTransportError("failed to write an EIP stdio frame") from error

    def _encode_outbound(self, frame: EIPTransportFrame) -> tuple[str, bytes, int]:
        if isinstance(frame, ControlFrame):
            if not isinstance(frame.payload, bytes):
                raise TypeError("control frame payload must be bytes")
            return _JSON_CONTENT_TYPE, frame.payload, self._max_request_bytes
        if isinstance(frame, DataFrame):
            try:
                payload = encode_data_frame(frame, max_frame_bytes=self._max_transfer_frame_bytes)
            except DataFrameCodecError as error:
                raise EIPProtocolError("invalid outbound EIP data frame") from error
            return _DATA_CONTENT_TYPE, payload, self._max_transfer_frame_bytes
        raise TypeError("unsupported EIP transport frame")

    async def _read_headers(self) -> bytes:
        header = bytearray()
        line_bytes = 0
        header_count = 0
        while not header.endswith(b"\r\n\r\n"):
            if len(header) == _MAX_HEADER_BYTES:
                raise EIPProtocolError("stdio response header exceeds its byte limit")
            if line_bytes == _MAX_HEADER_LINE_BYTES:
                raise EIPProtocolError("stdio response header line exceeds its byte limit")
            header.extend(await self._reader.readexactly(1))
            line_bytes += 1
            if header.endswith(b"\r\n"):
                if header.endswith(b"\r\n\r\n"):
                    break
                header_count += 1
                if header_count > _MAX_HEADER_COUNT:
                    raise EIPProtocolError("stdio response header count exceeds its limit")
                line_bytes = 0
        return bytes(header)


def _parse_headers(
    header: bytes,
    max_control_bytes: int,
    max_data_bytes: int,
) -> tuple[int, _FrameContentType]:
    try:
        text = header.decode("ascii")
    except UnicodeDecodeError as error:
        raise EIPProtocolError("stdio response header must be ASCII") from error

    values: dict[str, str] = {}
    for line in text[:-4].split("\r\n"):
        if ":" not in line:
            raise EIPProtocolError("malformed stdio response header")
        name, value = line.split(":", 1)
        if not name or not all(character.isascii() and (character.isalnum() or character == "-") for character in name):
            raise EIPProtocolError("malformed stdio response header name")
        normalized_name = name.lower()
        if normalized_name in values:
            raise EIPProtocolError("duplicate stdio response header")
        value = value.strip(" \t")
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise EIPProtocolError("malformed stdio response header value")
        values[normalized_name] = value

    if "content-length" not in values:
        raise EIPProtocolError("Content-Length response header is required")
    length_text = values["content-length"]
    if not length_text.isascii() or not length_text.isdecimal() or (length_text != "0" and length_text.startswith("0")):
        raise EIPProtocolError("Content-Length must be canonical decimal")
    content_length = int(length_text)
    content_type = _classify_content_type(values.get("content-type"))
    maximum = max_control_bytes if content_type is _FrameContentType.JSON else max_data_bytes
    if content_length > maximum:
        raise EIPProtocolError("stdio response body exceeds its applicable byte limit")

    for name in values:
        if name in _SECURITY_SENSITIVE_HEADERS or name.startswith("eip-"):
            raise EIPProtocolError("security-sensitive stdio response header is forbidden")
    return content_length, content_type


def _classify_content_type(value: str | None) -> _FrameContentType:
    if value is None or _valid_json_content_type(value):
        return _FrameContentType.JSON
    if value.lower() == _DATA_CONTENT_TYPE:
        return _FrameContentType.DATA
    raise EIPProtocolError("unsupported stdio Content-Type")


def _valid_json_content_type(value: str) -> bool:
    parts = [part.strip().lower() for part in value.split(";")]
    return parts in [["application/json"], ["application/json", "charset=utf-8"]]


async def _await_shared_close(close_task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            if close_task.done():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        break
    if cancelled:
        raise asyncio.CancelledError


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
