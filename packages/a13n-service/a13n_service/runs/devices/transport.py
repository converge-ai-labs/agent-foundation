"""Dedicated Device credentials admit bounded reverse EIP carriers, never browser sessions."""

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.websocket import AcceptedSocket, connection_token
from a13n_service.runs.devices.service import authenticate
from a13n_service.runs.environments.monitoring.collection import ReverseTarget, resolve
from a13n_service.runs.runtime import Runtime


async def connect(socket: WebSocket, device_id: str) -> None:
    carrier = AcceptedSocket(socket)
    runtime: Runtime = socket.app.state.runtime
    try:
        token = connection_token(socket, allow_loopback_ws=runtime.settings.monitoring.allow_loopback_ws)
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
