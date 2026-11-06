"""Control-owned EIP carriers and expiring, fenced discovery shared with Workers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from a13n_envd_client import AcceptedWebSocketTransport, EIPDeviceConnection
from a13n_envd_client.eip.v1 import DeviceDescriptor
from a13n_envd_client.websocket import WebSocketConnection
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.environments.clients.service import Attachment, validate_attachment
from a13n_service.runs.environments.schemas import ClientConnectionObservation, EnvironmentView

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

# All presence writes are conditional on the same exclusive connection claim.
# An old handler may neither publish online nor remove its successor's record.
_PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'PX', ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
redis.call('PUBLISH', KEYS[3], cjson.encode({state='online', epoch=ARGV[1]}))
return 1
"""
_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
redis.call('PEXPIRE', KEYS[2], ARGV[2])
return 1
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('PUBLISH', KEYS[3], cjson.encode({state='offline', epoch=ARGV[1]}))
return 1
"""


def owner_key(environment_id: str) -> str:
    return f"a13n:client-environment:{environment_id}:owner"


def presence_key(environment_id: str) -> str:
    return f"a13n:client-environment:{environment_id}:presence"


def connection_channel(environment_id: str) -> str:
    return f"a13n:client-environment:{environment_id}:connection"


class Presence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    epoch: str
    credential_version: int
    descriptor: DeviceDescriptor


@dataclass(slots=True)
class Connection:
    attachment: Attachment
    epoch: str
    device: EIPDeviceConnection | None = None
    carrier: WebSocketConnection | None = None

    async def disconnect(self) -> None:
        """Fence local admission and drop the carrier without closing native Sessions."""
        self.device = None
        if self.carrier is not None:
            await self.carrier.close()


class Connections:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.config = runtime.settings.client_environments
        self.entries: dict[str, Connection] = {}
        self.closed = False
        self.recovering = False
        self.lease_ms = int(max(3 * self.config.heartbeat_seconds, 2 * self.config.initialization_seconds) * 1000)

    async def presence(self, environment_id: str) -> Presence | None:
        """Discovery is transient evidence, never dispatch authority."""
        owner, value = await self.runtime.redis.mget(owner_key(environment_id), presence_key(environment_id))
        if owner is None or value is None:
            return None
        presence = Presence.model_validate_json(value)
        return presence if presence.epoch == owner else None

    async def observe(self, environments: list[EnvironmentView]) -> None:
        """Project live connection state for an authorized page in one Redis read."""
        clients = [item for item in environments if item.kind == "external"]
        if not clients:
            return
        keys = [key for item in clients for key in (owner_key(item.id), presence_key(item.id))]
        try:
            values = await self.runtime.redis.mget(keys)
        except RedisError:
            values = None
        observed_at = datetime.now(UTC)
        for index, item in enumerate(clients):
            state = "unknown"
            if values is not None:
                owner, raw = values[2 * index : 2 * index + 2]
                if owner is None:
                    state = "offline"
                elif raw is None:
                    state = "connecting"
                else:
                    presence = Presence.model_validate_json(raw)
                    state = "online" if presence.epoch == owner else "connecting"
            item.client_connection = ClientConnectionObservation(state=state, observed_at=observed_at)

    async def current(self, connection: Connection) -> None:
        environment_id = connection.attachment.environment_id
        if (
            self.closed
            or self.recovering
            or self.entries.get(environment_id) is not connection
            or await self.runtime.redis.get(owner_key(environment_id)) != connection.epoch
        ):
            raise ServiceError("unavailable", "Client environment connection ownership expired")
        await validate_attachment(self.runtime, connection.attachment)

    @asynccontextmanager
    async def reserve(self, attachment: Attachment) -> AsyncGenerator[Connection]:
        """Claim before upgrading, rejecting a second connection even during initialization."""
        identity = attachment.environment_id
        if self.closed:
            raise ServiceError("unavailable", "Client environment connections are closing")
        if self.recovering:
            raise ServiceError("unavailable", "Client environment connections are recovering")
        if identity in self.entries:
            raise ServiceError("conflict", "Client environment already has a connection")
        if len(self.entries) >= self.config.max_connections:
            raise ServiceError("unavailable", "Client environment connection capacity is exhausted")
        connection = Connection(attachment, uuid4().hex)
        self.entries[identity] = connection
        try:
            if not await self.runtime.redis.set(owner_key(identity), connection.epoch, nx=True, px=self.lease_ms):
                raise ServiceError("conflict", "Client environment already has a connection owner")
            await self.current(connection)
            yield connection
        finally:
            if self.entries.get(identity) is connection:
                del self.entries[identity]
            try:
                with anyio.move_on_after(self.config.initialization_seconds, shield=True):
                    await self.runtime.redis.eval(
                        _RELEASE,
                        3,
                        owner_key(identity),
                        presence_key(identity),
                        connection_channel(identity),
                        connection.epoch,
                    )
            finally:
                try:
                    with anyio.move_on_after(self.config.initialization_seconds, shield=True):
                        await self.runtime.client_sessions.drop_connection(connection)
                finally:
                    with anyio.move_on_after(self.config.initialization_seconds, shield=True):
                        if connection.device is not None:
                            await connection.device.close()
                        elif connection.carrier is not None:
                            await connection.carrier.close()

    async def attach(self, connection: Connection, carrier: WebSocketConnection) -> None:
        """Initialize as the EIP caller; no execution Session is opened by connecting."""
        connection.carrier = carrier
        attachment = connection.attachment
        identity = attachment.environment_id
        renewal = asyncio.create_task(self._renew(connection), name="client-environment-connection-renewal")
        routing = None
        try:
            device = await EIPDeviceConnection.initialize(
                AcceptedWebSocketTransport(carrier),
                expected_device_id=attachment.device_id,
                initialization_timeout=self.config.initialization_seconds,
            )
            connection.device = device
            ready = asyncio.Event()
            routing = asyncio.create_task(
                self.runtime.client_routing.serve(
                    identity,
                    connection.epoch,
                    self.runtime.client_sessions.dispatch,
                    ready=ready,
                    on_subscription_lost=carrier.close,
                ),
                name="client-environment-requests",
            )
            async with asyncio.timeout(self.config.initialization_seconds):
                await ready.wait()
            if routing.done() or not ready.is_set():
                raise ServiceError("unavailable", "Client environment request subscription is unavailable")
            await self.current(connection)
            online = Presence(
                epoch=connection.epoch,
                credential_version=attachment.credential_version,
                descriptor=device.descriptor,
            )
            if not await self.runtime.redis.eval(
                _PUBLISH,
                3,
                owner_key(identity),
                presence_key(identity),
                connection_channel(identity),
                connection.epoch,
                online.model_dump_json(),
                self.lease_ms,
            ):
                raise ServiceError("unavailable", "Client environment connection ownership expired")
            logger.info("Client environment connected", extra={"environment_id": identity, "epoch": connection.epoch})
            await carrier.wait_closed()
        finally:
            renewal.cancel()
            tasks = [renewal]
            if routing is not None:
                routing.cancel()
                tasks.append(routing)
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(
                "Client environment disconnected", extra={"environment_id": identity, "epoch": connection.epoch}
            )

    async def _renew(self, connection: Connection) -> None:
        identity = connection.attachment.environment_id
        try:
            while True:
                await asyncio.sleep(self.config.heartbeat_seconds)
                # Authority or coordination uncertainty closes the carrier. Renewal
                # never extends an unverified registration's execution access.
                with anyio.fail_after(self.config.heartbeat_seconds):
                    await self.current(connection)
                    retained = await self.runtime.redis.eval(
                        _RENEW, 2, owner_key(identity), presence_key(identity), connection.epoch, self.lease_ms
                    )
                    if not retained:
                        raise ServiceError("unavailable", "Client environment connection ownership expired")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Client environment connection retired",
                extra={"environment_id": identity, "exception_details": exception_details(error)},
            )
            if connection.carrier is not None:
                with anyio.move_on_after(1, shield=True):
                    await connection.carrier.close()

    async def close(self) -> None:
        self.closed = True
        await self.disconnect()

    async def disconnect(self) -> None:
        """An interrupted subscription retires the epochs that advertised that route."""
        # Handlers release their own claims, with expiry covering unreachable Redis.
        with anyio.move_on_after(self.config.initialization_seconds, shield=True):
            await asyncio.gather(
                *(entry.carrier.close() for entry in tuple(self.entries.values()) if entry.carrier is not None),
                return_exceptions=True,
            )
