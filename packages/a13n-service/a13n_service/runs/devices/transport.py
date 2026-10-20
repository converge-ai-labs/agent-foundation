"""Dedicated Device credentials admit bounded reverse EIP carriers, never browser sessions."""

import asyncio
import ipaddress

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from starlette.websockets import WebSocketState

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.devices.service import authenticate
from a13n_service.runs.environments.monitoring.collection import ReverseTarget, resolve
from a13n_service.runs.runtime import Runtime


class DeviceWebSocket:
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


async def connect(socket: WebSocket, device_id: str) -> None:
    carrier = DeviceWebSocket(socket)
    runtime: Runtime = socket.app.state.runtime
    try:
        values = socket.headers.getlist("authorization")
        scheme, _, token = values[0].partition(" ") if len(values) == 1 else ("", "", "")
        if (
            socket.query_params
            or socket.headers.get("origin") is not None
            or not secure(socket, allow_loopback_ws=runtime.settings.monitoring.allow_loopback_ws)
            or "eip.v1" not in socket.scope.get("subprotocols", [])
            or len(values) != 1
            or scheme.lower() != "bearer"
            or not 1 <= len(token) <= 4096
        ):
            await carrier.close(1008)
            return
        proxy = runtime.metrics_proxy
        if proxy is None:
            raise ServiceError("unavailable", "Monitoring is unavailable")
        with anyio.fail_after(runtime.settings.monitoring.request_seconds):
            digest = await authenticate(runtime, device_id, token)
            selection = await resolve(runtime, device_id)
            if (
                selection is None
                or not isinstance(selection.target, ReverseTarget)
                or selection.target.token_hash != digest
            ):
                raise ServiceError("unauthenticated", "Select an enabled proxy source before connecting")
        async with proxy.reverse_connection(selection) as connection:
            await socket.accept(subprotocol="eip.v1")
            await connection.attach(selection.device_id, carrier)
    except (ServiceError, EnvironmentProviderError, RedisError, TimeoutError, WebSocketDisconnect, OSError):
        with anyio.move_on_after(1, shield=True):
            await carrier.close(1008)
    finally:
        with anyio.move_on_after(1, shield=True):
            await carrier.close()


def install(app: FastAPI) -> None:
    app.add_api_websocket_route("/api/v1/devices/{device_id}/connect", connect)
