"""Control ingress capacity, ticket admission and owned socket task lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from a13n_logging import get_logger
from anyio import move_on_after
from redis.asyncio import Redis
from starlette.websockets import WebSocket, WebSocketDisconnect

from a13n_service.ids import new_object_id

from .connection_session import ClientConnectionSession, UseAuthorizer
from .coordination import CoordinationError
from .relay_storage import ConnectionRelayStore
from .service import ClientConnectionService
from .transport import ClientWebSocket

logger = get_logger(__name__)


class ClientConnectionHost:
    def __init__(
        self,
        service: ClientConnectionService,
        redis: Redis,
        authorize_use: UseAuthorizer,
        *,
        max_connections: int = 128,
    ) -> None:
        if not 1 <= max_connections <= 1024:
            raise ValueError("Control reverse connection capacity must be bounded")
        self._service, self._redis, self._authorize_use = service, redis, authorize_use
        self.instance_id = new_object_id("eco")
        self._capacity = max_connections
        self._active: dict[asyncio.Task[None], Callable[[], None]] = {}
        self._draining = False

    async def serve(self, websocket: WebSocket, environment_id: str) -> None:
        current = asyncio.current_task()
        assert current is not None
        if self._draining or len(self._active) >= self._capacity:
            await websocket.close(code=1013)
            return
        carrier = ClientWebSocket(websocket)
        self._active[current] = carrier.invalidate
        connection_id: str | None = None
        identity = None
        try:
            ticket = self._ticket(websocket)
            target = await self._service.resources.resolve(environment_id)
            admission = await self._service.coordination.admit(
                target.organization_id, environment_id, ticket=ticket, owner_instance_id=self.instance_id
            )
            identity = admission.value.connection
            if identity is None:
                raise CoordinationError("candidate_expired")
            connection_id = identity.connection_id
            session = ClientConnectionSession(
                self._service, carrier, admission, ConnectionRelayStore(self._redis, identity), self._authorize_use
            )
            self._active[current] = session.begin_drain
            await websocket.accept(subprotocol="eip.v1")
            async with asyncio.TaskGroup() as tasks:
                reader = tasks.create_task(carrier.read_messages(), name="client-environment-socket-reader")
                lifetime = tasks.create_task(session.run(), name="client-environment-connection")
                try:
                    await asyncio.wait((reader, lifetime), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    reader.cancel()
                    lifetime.cancel()
        except asyncio.CancelledError:
            raise
        except WebSocketDisconnect:
            pass
        except Exception as error:
            # The SDK and storage may carry native diagnostics. The connection
            # read exposes bounded errors; logs retain only public correlation.
            logger.warning(
                "client_environment_connection_closed",
                extra={
                    "environment_id": environment_id,
                    "connection_id": connection_id,
                    "exception_type": type(error).__name__,
                },
            )
        finally:
            carrier.invalidate()
            with move_on_after(1.5, shield=True):
                await carrier.close()
                if identity is not None:
                    try:
                        await self._service.coordination.abandon(
                            identity, error="control_draining" if self._draining else "environment_unavailable"
                        )
                    except CoordinationError:
                        pass
            self._active.pop(current, None)

    @staticmethod
    def _ticket(websocket: WebSocket) -> str:
        values = websocket.headers.getlist("authorization")
        if (
            len(values) != 1
            or websocket.scope.get("query_string")
            or "eip.v1" not in websocket.scope.get("subprotocols", ())
        ):
            raise CoordinationError("ticket_invalid")
        scheme, separator, ticket = values[0].partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not 32 <= len(ticket) <= 256
            or any(c.isspace() for c in ticket)
        ):
            raise CoordinationError("ticket_invalid")
        return ticket

    def begin_drain(self) -> None:
        if self._draining:
            return
        self._draining = True
        for task, invalidate in tuple(self._active.items()):
            invalidate()
            task.cancel()

    async def close(self) -> None:
        self.begin_drain()
        tasks = tuple(self._active)
        if tasks:
            with move_on_after(5, shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
