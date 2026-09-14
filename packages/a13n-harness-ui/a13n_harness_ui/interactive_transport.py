"""Browser-compatible first-frame authentication for interactive WebUI routes."""

from __future__ import annotations

import hmac

from anyio import fail_after
from pydantic import Field, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from a13n_harness_ui.surfaces import SurfaceModel


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
