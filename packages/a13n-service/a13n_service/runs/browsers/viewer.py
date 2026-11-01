"""Relay private Browser VNC connections using their existing finite input grant.

The guest owns expiry/revocation, including established connections while Service
is offline. Only a currently authorized, published grant can enter this relay.
"""

import secrets
from urllib.parse import parse_qs, urlsplit

import anyio
from fastapi import Request, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

from a13n_service.infra.db import now, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.browsers import control, lifecycle
from a13n_service.runs.browsers.schemas import Viewer
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.runtime import Runtime

PROTOCOL = "a13n.browser."


def token_of(grant: Viewer) -> str:
    return parse_qs(urlsplit(grant.url).query).get("token", [""])[0]


def public_viewer(request: Request, browser_id: str, grant: Viewer) -> Viewer:
    if grant.protocol == "vnc":
        # Keep bearer credentials out of URLs, access logs and browser history.
        grant.token = token_of(grant)
        grant.url = str(request.url_for("browser_viewer_connect", browser_id=browser_id))
    return grant


async def authorize(runtime: Runtime, browser_id: str, token: str) -> Viewer:
    async with short_session(runtime.storage) as session:
        row = await session.get(BrowserRow, browser_id)
        current = await now(session)
        if (
            row is None
            or row.status != "ready"
            or row.control != "human"
            or row.viewer is None
            or row.control_expires_at is None
            or row.control_expires_at <= current
            or (row.expires_at is not None and row.expires_at <= current)
        ):
            raise ServiceError("forbidden", "Browser viewer is unavailable")
        grant = Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer))
        if (
            grant.protocol != "vnc"
            or grant.episode != row.episode
            or grant.generation != row.generation
            or grant.expires_at <= current
            or not token
            or not secrets.compare_digest(token_of(grant), token)
            or not await control.controller_allowed(session, runtime, row)
        ):
            raise ServiceError("forbidden", "Browser viewer is unavailable")
        return grant


async def relay(socket: WebSocket, browser_id: str) -> None:
    runtime: Runtime = socket.app.state.runtime
    offered = socket.scope.get("subprotocols", [])
    tokens = [value.removeprefix(PROTOCOL) for value in offered if value.startswith(PROTOCOL)]
    try:
        if socket.query_params or len(tokens) != 1 or "binary" not in offered:
            raise ServiceError("forbidden", "Browser viewer credential is required")
        with anyio.fail_after(15):
            grant = await authorize(runtime, browser_id, tokens[0])
        # The destination comes exclusively from the encrypted supplier grant.
        async with connect(
            grant.url, subprotocols=[Subprotocol("binary")], proxy=None, open_timeout=15, max_size=16 * 1024 * 1024
        ) as upstream:
            await socket.accept(subprotocol="binary")
            async with anyio.create_task_group() as tasks:

                async def to_guest() -> None:
                    try:
                        while True:
                            message = await socket.receive()
                            if message["type"] == "websocket.disconnect":
                                break
                            data = message.get("bytes")
                            if not isinstance(data, bytes):
                                break
                            await upstream.send(data)
                    except (WebSocketDisconnect, WebSocketException, OSError):
                        pass
                    finally:
                        tasks.cancel_scope.cancel()

                async def to_client() -> None:
                    try:
                        async for message in upstream:
                            if not isinstance(message, bytes):
                                break
                            await socket.send_bytes(message)
                    except (WebSocketDisconnect, WebSocketException, OSError):
                        pass
                    finally:
                        tasks.cancel_scope.cancel()

                tasks.start_soon(to_guest)
                tasks.start_soon(to_client)
    except (ServiceError, WebSocketException, WebSocketDisconnect, OSError, TimeoutError):
        pass
    finally:
        if socket.application_state is not WebSocketState.DISCONNECTED:
            with anyio.move_on_after(1, shield=True):
                await socket.close(code=1008)
