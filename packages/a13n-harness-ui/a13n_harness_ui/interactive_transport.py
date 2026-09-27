"""Browser-compatible first-frame authentication for interactive WebUI routes."""

from __future__ import annotations

import hmac

from anyio import Lock, fail_after
from pydantic import Field, ValidationError
from starlette.types import Message
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from a13n_harness_ui.surfaces import SurfaceModel


class InteractiveOutput:
    """One bounded send/close owner for concurrent browser stream producers."""

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self._lock = Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return (
            self._closed
            or self.socket.application_state is WebSocketState.DISCONNECTED
            or self.socket.client_state is WebSocketState.DISCONNECTED
        )

    async def send(self, frame: SurfaceModel) -> None:
        with fail_after(10):
            async with self._lock:
                if self.closed:
                    raise WebSocketDisconnect(code=1001)
                await self._send({"type": "websocket.send", "text": frame.model_dump_json()})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        with fail_after(10):
            async with self._lock:
                if self.closed:
                    return
                self._closed = True
                await self._send({"type": "websocket.close", "code": code, "reason": reason})

    async def _send(self, message: Message) -> None:
        try:
            await self.socket.send(message)
        except WebSocketDisconnect:
            self._closed = True
            raise
        except RuntimeError as exc:
            # Uvicorn may close its transport before Starlette receives the
            # disconnect. Starlette versions also use RuntimeError (or its
            # WebSocketDisconnected subclass) for sends after close. Normalize
            # only these known closed-transport errors, not application defects.
            kind = message["type"]
            if str(exc) not in {
                'Cannot call "send" once a close message has been sent.',
                f"Unexpected ASGI message '{kind}', after sending 'websocket.close'.",
                f"Unexpected ASGI message '{kind}', after sending 'websocket.close' or response already completed.",
            }:
                raise
            self._closed = True
            raise WebSocketDisconnect(code=1001) from exc


class InteractiveAuthentication(SurfaceModel):
    api_key: str = Field(default="", max_length=4096)


async def receive_text(socket: WebSocket, *, limit: int) -> str:
    message = await socket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if not isinstance(text, str) or len(text) > limit:
        raise ValueError("Expected a bounded text frame")
    return text


async def authenticate_interactive(socket: WebSocket, api_key: str | None) -> bool:
    # Host/Origin are checked by AccessBoundary before accept. The key never
    # appears in a query string, negotiated subprotocol or diagnostic response.
    await socket.accept()
    try:
        with fail_after(10):
            raw = await receive_text(socket, limit=8192)
        auth = InteractiveAuthentication.model_validate_json(raw)
        if api_key is not None and not hmac.compare_digest(auth.api_key.encode(), api_key.encode()):
            raise ValueError("Authentication failed")
    except WebSocketDisconnect:
        return False
    except (TimeoutError, ValueError, ValidationError):
        await socket.close(code=4401, reason="Authentication required")
        return False
    return True
