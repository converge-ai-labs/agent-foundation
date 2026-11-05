"""Accepted EIP WebSocket framing and ingress transport checks."""

import asyncio
import ipaddress

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from a13n_service.infra.errors import ServiceError


class AcceptedSocket:
    subprotocol = "eip.v1"

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.closed = asyncio.Event()

    async def send(self, message: str | bytes) -> None:
        try:
            if isinstance(message, str):
                await self.socket.send_text(message)
            else:
                await self.socket.send_bytes(message)
        except (WebSocketDisconnect, OSError) as error:
            self.closed.set()
            raise EOFError("Device disconnected") from error

    async def recv(self) -> str | bytes:
        try:
            message = await self.socket.receive()
        except (WebSocketDisconnect, OSError) as error:
            self.closed.set()
            raise EOFError("Device disconnected") from error
        if message["type"] == "websocket.disconnect":
            self.closed.set()
            raise EOFError("Device disconnected")
        value = message.get("text") if message.get("text") is not None else message.get("bytes")
        if not isinstance(value, str | bytes):
            raise OSError("Unsupported Device frame")
        return value

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        if self.socket.application_state is not WebSocketState.DISCONNECTED:
            await self.socket.close(code=code, reason=reason)

    async def wait_closed(self) -> None:
        await self.closed.wait()


def secure(socket: WebSocket, *, allow_loopback_ws: bool = False) -> bool:
    if socket.url.scheme == "wss":
        return True
    if not allow_loopback_ws:
        return False
    try:
        return socket.client is not None and ipaddress.ip_address(socket.client.host).is_loopback
    except ValueError:
        return False


def connection_token(socket: WebSocket, *, allow_loopback_ws: bool = False) -> str:
    """Dedicated non-browser bearer authentication over an explicitly admitted carrier."""
    values = socket.headers.getlist("authorization")
    scheme, _, token = values[0].partition(" ") if len(values) == 1 else ("", "", "")
    if (
        socket.query_params
        or socket.headers.get("origin") is not None
        or not secure(socket, allow_loopback_ws=allow_loopback_ws)
        or "eip.v1" not in socket.scope.get("subprotocols", [])
        or len(values) != 1
        or scheme.lower() != "bearer"
        or not 1 <= len(token) <= 4096
    ):
        raise ServiceError("unauthenticated", "Invalid client connection authentication")
    return token
