"""Dedicated client credentials attach execution carriers without registering or mounting them."""

import anyio
from a13n_envd_client.errors import EIPClientError
from a13n_logging import exception_details, get_logger
from fastapi import WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response
from starlette.websockets import WebSocketState

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.websocket import AcceptedSocket, connection_token
from a13n_service.runs.environments.clients.service import authenticate
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)


async def connect(socket: WebSocket, environment_id: str) -> None:
    carrier = AcceptedSocket(socket)
    runtime: Runtime = socket.app.state.runtime
    config = runtime.settings.client_environments
    try:
        token = connection_token(socket, allow_loopback_ws=config.allow_loopback_ws)
        with anyio.fail_after(config.initialization_seconds):
            attachment = await authenticate(runtime, environment_id, token)
        async with runtime.client_connections.reserve(attachment) as connection:
            await socket.accept(subprotocol="eip.v1")
            await runtime.client_connections.attach(connection, carrier)
    except (
        ServiceError,
        EIPClientError,
        RedisError,
        SQLAlchemyError,
        TimeoutError,
        WebSocketDisconnect,
        OSError,
    ) as error:
        logger.info(
            "Client environment connection ended",
            extra={"environment_id": environment_id, "exception_details": exception_details(error)},
        )
        with anyio.move_on_after(1, shield=True):
            temporary = isinstance(error, (RedisError, SQLAlchemyError, TimeoutError)) or (
                isinstance(error, ServiceError) and error.code in {"unavailable", "conflict"}
            )
            if temporary and socket.application_state is WebSocketState.CONNECTING:
                # A close before upgrade becomes HTTP 403, which envd correctly
                # treats as a permanent credential failure rather than retrying.
                await socket.send_denial_response(Response(status_code=503, headers={"Retry-After": "1"}))
            else:
                await carrier.close(1013 if temporary else 1008)
    finally:
        with anyio.move_on_after(1, shield=True):
            await carrier.close()
