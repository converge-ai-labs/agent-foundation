"""ASGI adapter for an authenticated reverse EIP Device carrier."""

from __future__ import annotations

from anyio import Event
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState


class DeviceWebSocket:
    subprotocol = "eip.v1"

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._closed = Event()

    async def send(self, message: str | bytes) -> None:
        try:
            if isinstance(message, str):
                await self._socket.send_text(message)
            else:
                await self._socket.send_bytes(message)
        except (WebSocketDisconnect, OSError) as error:
            self._closed.set()
            raise EOFError("Device disconnected") from error

    async def recv(self) -> str | bytes:
        try:
            message = await self._socket.receive()
        except (WebSocketDisconnect, OSError) as error:
            self._closed.set()
            raise EOFError("Device disconnected") from error
        if message["type"] == "websocket.disconnect":
            self._closed.set()
            raise EOFError("Device disconnected")
        value = message.get("text") if message.get("text") is not None else message.get("bytes")
        if not isinstance(value, str | bytes):
            raise OSError("Unsupported Device frame")
        return value

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._socket.application_state is not WebSocketState.DISCONNECTED:
            await self._socket.close(code=code, reason=reason)

    async def wait_closed(self) -> None:
        await self._closed.wait()
