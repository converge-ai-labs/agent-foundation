"""Bounded ASGI carrier with one reader and lease-gated EIP writes."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from a13n_envd_client import EIPSessionStateError
from a13n_envd_client.eip.v1 import JsonRpcRequest
from pydantic import ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from .authority import ConnectionIdentity, DispatchAuthority, DispatchDenied


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
        self._invalidated = False
        self._connection: DispatchAuthority | None = None
        self._scope: ContextVar[DispatchAuthority | None] = ContextVar("eip_dispatch_scope", default=None)
        self._scope_required = False

    def bind_connection(self, authority: DispatchAuthority) -> None:
        if self._invalidated or self._closed.is_set():
            raise DispatchDenied("Client WebSocket cannot regain dispatch authority")
        if self._connection is not None or not isinstance(authority.identity, ConnectionIdentity):
            raise ValueError("A carrier can bind exactly one connection authority")
        authority.check(authority.identity)
        self._connection = authority

    def require_scope(self) -> None:
        """End initialization; new operations need Device-read or binding authority."""
        self._scope_required = True

    @contextmanager
    def dispatch_scope(self, authority: DispatchAuthority) -> Iterator[None]:
        identity = authority.identity
        if (
            self._connection is None
            or isinstance(identity, ConnectionIdentity)
            or identity.connection != self._connection.identity
        ):
            raise ValueError("Dispatch scope must belong to this exact connection")
        authority.check(identity)
        token = self._scope.set(authority)
        try:
            # SDK sends, keepalive and transfer tasks inherit this exact authority.
            yield
        finally:
            self._scope.reset(token)

    def invalidate(self) -> None:
        self._invalidated = True
        if self._connection is not None:
            self._connection.invalidate()

    async def send(self, message: str | bytes) -> None:
        if self._closed.is_set():
            raise EOFError("Client WebSocket is closed")
        if self._invalidated:
            raise OSError("Client WebSocket dispatch authority is unavailable")
        connection = self._connection
        if connection is None:
            raise OSError("Client WebSocket has no dispatch authority")
        try:
            async with connection.write(connection.identity):
                scope = self._scope.get()
                if _is_session_close(message):
                    # Closing an SDK-owned Session is connection cleanup, not new
                    # execution authority. It must work after use expiry/cancellation,
                    # including cleanup of a late session.open response.
                    await self._send(message)
                elif scope is None:
                    if self._scope_required:
                        raise EIPSessionStateError("Client WebSocket requires dispatch scope authority")
                    await self._send(message)
                else:
                    try:
                        async with scope.write(scope.identity):
                            await self._send(message)
                    except DispatchDenied as error:
                        raise EIPSessionStateError("Client WebSocket scope authority is unavailable") from error
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
        self.invalidate()
        self._closed.set()
        self._available.set()


def _is_session_close(message: str | bytes) -> bool:
    if not isinstance(message, str):
        return False
    try:
        request = JsonRpcRequest.model_validate_json(message)
    except ValidationError:
        return False
    return request.method == "session.close" and request.eip_session is not None
