from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from converge_agent_envd_client.eip.v1 import (
    DataFrame,
    DataFrameCodecError,
    decode_data_frame,
    encode_data_frame,
)
from converge_agent_envd_client.errors import EIPProtocolError, EIPTransportClosedError, EIPTransportError
from converge_agent_envd_client.transport import ControlFrame, EIPTransportFrame

_EIP_SUBPROTOCOL = "eip.v1"
_CLOSE_GRACE_SECONDS = 1.0


class AcceptedWebSocketTransport:
    """EIP framing over a control-service-authenticated WebSocket connection.

    The owning listener validates the Bearer attachment token before constructing
    this adapter. This class verifies the negotiated EIP subprotocol and owns only
    post-upgrade EIP message framing.
    """

    def __init__(
        self,
        connection: ServerConnection,
        *,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
        max_transfer_frame_bytes: int = 1024 * 1024,
    ) -> None:
        if connection.subprotocol != _EIP_SUBPROTOCOL:
            raise ValueError("accepted WebSocket must negotiate eip.v1")
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        _validate_limit("max_transfer_frame_bytes", max_transfer_frame_bytes)
        self._connection = connection
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._max_transfer_frame_bytes = max_transfer_frame_bytes
        self._send_lock = asyncio.Lock()
        self._receive_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

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
            raise EIPTransportClosedError("WebSocket transport is closed")
        if isinstance(frame, ControlFrame):
            if not isinstance(frame.payload, bytes):
                raise TypeError("control frame payload must be bytes")
            if len(frame.payload) > self._max_request_bytes:
                raise EIPTransportError("EIP control message exceeds its negotiated byte limit")
            try:
                payload: str | bytes = frame.payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise EIPProtocolError("EIP control message must be UTF-8 JSON") from error
        elif isinstance(frame, DataFrame):
            try:
                payload = encode_data_frame(frame, max_frame_bytes=self._max_transfer_frame_bytes)
            except DataFrameCodecError as error:
                raise EIPProtocolError("invalid outbound EIP data frame") from error
        else:
            raise TypeError("unsupported EIP transport frame")

        async with self._send_lock:
            if self._closed:
                raise EIPTransportClosedError("WebSocket transport is closed")
            try:
                await self._connection.send(payload)
            except ConnectionClosed as error:
                raise EIPTransportClosedError("WebSocket transport closed while sending") from error
            except OSError as error:
                raise EIPTransportError("failed to send an EIP WebSocket message") from error

    async def receive(self) -> EIPTransportFrame:
        if self._closed:
            raise EIPTransportClosedError("WebSocket transport is closed")
        async with self._receive_lock:
            if self._closed:
                raise EIPTransportClosedError("WebSocket transport is closed")
            try:
                message = await self._connection.recv()
            except ConnectionClosed as error:
                raise EIPTransportClosedError("WebSocket transport closed while receiving") from error
            except OSError as error:
                raise EIPTransportError("failed to receive an EIP WebSocket message") from error

        if isinstance(message, str):
            payload = message.encode("utf-8")
            if len(payload) > self._max_response_bytes:
                await self._fail_protocol()
                raise EIPProtocolError("EIP control message exceeds its negotiated byte limit")
            return ControlFrame(payload)
        if len(message) > self._max_transfer_frame_bytes:
            await self._fail_protocol()
            raise EIPProtocolError("EIP data message exceeds its negotiated byte limit")
        try:
            return decode_data_frame(message, max_frame_bytes=self._max_transfer_frame_bytes)
        except DataFrameCodecError as error:
            await self._fail_protocol()
            raise EIPProtocolError("invalid EIP WebSocket data frame") from error

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close(), name="eip-websocket-close")
        await _await_shared_close(self._close_task)

    async def _fail_protocol(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(
                self._finish_close(code=1002),
                name="eip-websocket-protocol-close",
            )
        await _await_shared_close(self._close_task)

    async def _finish_close(self, *, code: int = 1000) -> None:
        try:
            async with asyncio.timeout(_CLOSE_GRACE_SECONDS):
                await self._connection.close(code=code)
        except (ConnectionClosed, OSError, TimeoutError):
            pass


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


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
