"""Bounded ASGI carrier with one reader and lease-gated EIP writes."""

from __future__ import annotations

import asyncio
from collections import deque

from starlette.websockets import WebSocket, WebSocketDisconnect

from .authority import ConnectionIdentity, DispatchAuthority, DispatchDenied, UseIdentity


class ClientWebSocket:
    """Host adapter for the shared SDK's AcceptedWebSocketTransport.

    The owning task group runs ``read_messages`` once, including while a takeover
    candidate waits. Only SDK ``recv`` consumes its bounded mailbox; wait_closed
    never reads the socket. Binding authority does not initialize the EIP Session.
    """

    subprotocol = "eip.v1"

    def __init__(self, websocket: WebSocket, *, max_messages: int = 16, max_message_bytes: int = 1024 * 1024) -> None:
        if min(max_messages, max_message_bytes) < 1:
            raise ValueError("Client WebSocket mailbox limits must be positive")
        self._websocket = websocket
        self._max_messages = max_messages
        self._max_message_bytes = max_message_bytes
        self._messages: deque[str | bytes] = deque()
        self._available = asyncio.Event()
        self._closed = asyncio.Event()
        self._reader_started = False
        self._connection: DispatchAuthority | None = None
        self._use: DispatchAuthority | None = None
        self._use_required = False

    def bind_connection(self, authority: DispatchAuthority) -> None:
        if self._connection is not None or not isinstance(authority.identity, ConnectionIdentity):
            raise ValueError("A carrier can bind exactly one connection authority")
        authority.check(authority.identity)
        self._connection = authority

    def require_use(self) -> None:
        """End connection-only initialization; no later write can bypass use authority."""
        self._use_required = True

    def bind_use(self, authority: DispatchAuthority) -> None:
        if (
            self._connection is None
            or self._use is not None
            or not isinstance(authority.identity, UseIdentity)
            or authority.identity.connection != self._connection.identity
        ):
            raise ValueError("A carrier can bind exactly one use of its connection")
        self._connection.check(self._connection.identity)
        authority.check(authority.identity)
        self._use = authority
        self._use_required = True

    async def send(self, message: str | bytes) -> None:
        if self._closed.is_set():
            raise EOFError("Client WebSocket is closed")
        connection = self._connection
        if connection is None:
            raise OSError("Client WebSocket has no dispatch authority")
        try:
            async with connection.write(connection.identity):
                if self._use is not None:
                    async with self._use.write(self._use.identity):
                        await self._send(message)
                elif self._use_required:
                    raise DispatchDenied("Client WebSocket requires use authority")
                else:
                    await self._send(message)
        except (DispatchDenied, TimeoutError) as error:
            raise OSError("Client WebSocket dispatch authority is unavailable") from error

    async def _send(self, message: str | bytes) -> None:
        if self._closed.is_set():
            raise EOFError("Client WebSocket is closed")
        try:
            if isinstance(message, str):
                await self._websocket.send_text(message)
            else:
                await self._websocket.send_bytes(message)
        except WebSocketDisconnect as error:
            self._mark_closed()
            raise EOFError("Client WebSocket is closed") from error

    async def read_messages(self) -> None:
        if self._reader_started:
            raise RuntimeError("Client WebSocket already has its receive owner")
        self._reader_started = True
        try:
            while not self._closed.is_set():
                event = await self._websocket.receive()
                if event["type"] == "websocket.disconnect":
                    return
                message = event.get("text")
                if message is None:
                    message = event.get("bytes")
                if not isinstance(message, str | bytes):
                    await self.close(code=1002)
                    return
                size = len(message.encode("utf-8")) if isinstance(message, str) else len(message)
                if len(self._messages) >= self._max_messages or size > self._max_message_bytes:
                    await self.close(code=1009)
                    return
                self._messages.append(message)
                self._available.set()
        except (WebSocketDisconnect, OSError):
            pass
        finally:
            self._mark_closed()

    async def recv(self) -> str | bytes:
        while True:
            if self._messages:
                return self._messages.popleft()
            if self._closed.is_set():
                raise EOFError("Client WebSocket is closed")
            self._available.clear()
            await self._available.wait()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._closed.is_set():
            return
        self._mark_closed()
        try:
            async with asyncio.timeout(1):
                await self._websocket.close(code=code, reason=reason)
        except (WebSocketDisconnect, OSError, TimeoutError):
            pass

    async def wait_closed(self) -> None:
        await self._closed.wait()

    def _mark_closed(self) -> None:
        self._closed.set()
        self._available.set()
