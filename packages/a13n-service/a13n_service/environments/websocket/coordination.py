"""Atomic, bounded Redis connection admission and exclusive Attempt use."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.ids import new_object_id
from a13n_service.storage.redis import redis_memory_identity

from .authority import ConnectionIdentity, LeaseDeadline, UseIdentity

_SCRIPT = files(__package__).joinpath("coordination.lua").read_text()
# Connection requests and Worker responses must share a Redis scripting domain
# for atomic terminal response/ACK. This explicit hash tag supports Redis Cluster.
KEY_PREFIX = "a13n:{environment-relay}"


def environment_key(organization_id: str, environment_id: str) -> str:
    digest = hashlib.sha256(f"{organization_id}\0{environment_id}".encode()).hexdigest()
    return f"{KEY_PREFIX}:connection:{digest}"


def ticket_key(secret: str) -> str:
    return f"{KEY_PREFIX}:ticket:{hashlib.sha256(secret.encode()).hexdigest()}"


class CoordinationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Client Environment coordination could not complete")


@dataclass(frozen=True, slots=True)
class CoordinationLimits:
    lease_ms: int = 2_000
    ticket_ms: int = 30_000
    candidate_ms: int = 8_000
    retention_ms: int = 60_000
    safety_margin_seconds: float = 0.05

    def __post_init__(self) -> None:
        if min(self.lease_ms, self.ticket_ms, self.candidate_ms) <= 0:
            raise ValueError("Connection coordination limits must be positive")
        if self.retention_ms <= max(self.lease_ms, self.ticket_ms, self.candidate_ms):
            raise ValueError("Connection evidence retention must exceed all grant horizons")
        if not 0 <= self.safety_margin_seconds < self.lease_ms / 1000:
            raise ValueError("Clock margin must be smaller than the connection lease")


DEFAULT_LIMITS = CoordinationLimits()
type ConnectionFailure = Literal["environment_unavailable", "environment_initialization_failed", "control_draining"]


@dataclass(frozen=True, slots=True)
class ConnectionTicket:
    secret: str = field(repr=False)
    connection_id: str
    expires_at_ms: int


class _ReplyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Retirement(_ReplyModel):
    identity: ConnectionIdentity
    until_ms: int
    acknowledged: bool


class UseGrant(_ReplyModel):
    identity: UseIdentity
    expires_at_ms: int


class ConnectionObservation(_ReplyModel):
    code: Literal["ok"]
    now_ms: int
    status: Literal["online", "connecting", "offline"]
    connection: ConnectionIdentity | None
    expires_at_ms: int
    barrier_ms: int
    retiring: Retirement | None
    use: UseGrant | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ConfirmedObservation:
    value: ConnectionObservation
    request_started_at: float
    safety_margin_seconds: float

    def deadline(self, *, use: bool = False) -> LeaseDeadline:
        expires = self.value.expires_at_ms
        if use:
            if self.value.use is None:
                raise CoordinationError("authority_lost")
            expires = min(expires, self.value.use.expires_at_ms)
        return LeaseDeadline.confirmed(
            request_started_at=self.request_started_at,
            server_now_ms=self.value.now_ms,
            expires_at_ms=expires,
            safety_margin_seconds=self.safety_margin_seconds,
        )


class ConnectionCoordination:
    def __init__(self, redis: Redis, *, limits: CoordinationLimits = DEFAULT_LIMITS) -> None:
        self._script = redis.register_script(_SCRIPT)
        self._memory_server_id = redis_memory_identity(redis)
        self.limits = limits

    async def issue(self, organization_id: str, environment_id: str) -> ConnectionTicket:
        secret = "ect_" + secrets.token_urlsafe(32)
        connection_id = new_object_id("ec")
        result, _ = await self._call(
            organization_id,
            environment_id,
            "issue",
            secret,
            {
                "connection_id": connection_id,
                "organization_id": organization_id,
                "environment_id": environment_id,
            },
        )
        expires = result.get("expires_at_ms")
        if not isinstance(expires, int):
            raise CoordinationError("coordination_unavailable")
        return ConnectionTicket(secret, connection_id, expires)

    async def admit(
        self, organization_id: str, environment_id: str, *, ticket: str, owner_instance_id: str
    ) -> ConfirmedObservation:
        if not 32 <= len(ticket) <= 256:
            raise CoordinationError("ticket_invalid")
        connection = ConnectionIdentity(organization_id, environment_id, "", new_object_id("ece"), owner_instance_id)
        return await self._observe_call(
            organization_id, environment_id, "admit", ticket=ticket, payload={"connection": asdict(connection)}
        )

    async def observe(self, organization_id: str, environment_id: str) -> ConfirmedObservation:
        return await self._observe_call(organization_id, environment_id, "observe")

    async def promote(self, connection: ConnectionIdentity) -> ConfirmedObservation:
        return await self._connection_call("promote", connection)

    async def online(self, connection: ConnectionIdentity) -> ConfirmedObservation:
        return await self._connection_call("online", connection)

    async def renew(self, connection: ConnectionIdentity) -> ConfirmedObservation:
        return await self._connection_call("renew", connection)

    async def acquire_use(self, identity: UseIdentity, *, attempt_expires_at_ms: int) -> ConfirmedObservation:
        return await self._connection_call(
            "acquire_use", identity.connection, use=_use_payload(identity), attempt_expires_at_ms=attempt_expires_at_ms
        )

    async def renew_use(self, identity: UseIdentity, *, attempt_expires_at_ms: int) -> ConfirmedObservation:
        return await self._connection_call(
            "renew_use", identity.connection, use=_use_payload(identity), attempt_expires_at_ms=attempt_expires_at_ms
        )

    async def release_use(self, identity: UseIdentity) -> None:
        """Retire only this exact use; only Control can acknowledge socket detachment."""
        await self._connection_call("release_use", identity.connection, use=_use_payload(identity))

    async def retire(
        self, connection: ConnectionIdentity, *, error: ConnectionFailure = "environment_unavailable"
    ) -> None:
        await self._connection_call("retire", connection, error=error)

    async def acknowledge(self, connection: ConnectionIdentity) -> None:
        await self._connection_call("acknowledge", connection)

    async def abandon(
        self, connection: ConnectionIdentity, *, error: ConnectionFailure = "environment_unavailable"
    ) -> None:
        await self._connection_call("abandon", connection, error=error)

    async def _connection_call(
        self, operation: str, connection: ConnectionIdentity, **payload: object
    ) -> ConfirmedObservation:
        return await self._observe_call(
            connection.organization_id,
            connection.environment_id,
            operation,
            payload={"connection": asdict(connection), **payload},
        )

    async def _observe_call(
        self,
        organization: str,
        environment: str,
        operation: str,
        *,
        ticket: str = "",
        payload: dict[str, object] | None = None,
    ) -> ConfirmedObservation:
        result, started = await self._call(organization, environment, operation, ticket, payload or {})
        try:
            observation = ConnectionObservation.model_validate(result)
        except ValidationError as error:
            raise CoordinationError("coordination_unavailable") from error
        return ConfirmedObservation(observation, started, self.limits.safety_margin_seconds)

    async def _call(
        self, organization: str, environment: str, operation: str, ticket: str, payload: dict[str, object]
    ) -> tuple[dict[str, object], float]:
        request = {
            **asdict(self.limits),
            "memory_server_id": self._memory_server_id,
            "operation": operation,
            **payload,
        }
        started = monotonic()
        try:
            async with asyncio.timeout(min(1, self.limits.lease_ms / 2000)):
                raw = await self._script(
                    keys=[environment_key(organization, environment), ticket_key(ticket)],
                    args=[json.dumps(request, separators=(",", ":"), allow_nan=False)],
                )
            result = json.loads(raw)
        except (RedisError, ValueError, TypeError, TimeoutError) as error:
            raise CoordinationError("coordination_unavailable") from error
        if not isinstance(result, dict) or not isinstance(result.get("code"), str):
            raise CoordinationError("coordination_unavailable")
        if result["code"] != "ok":
            raise CoordinationError(result["code"])
        return result, started


def _use_payload(identity: UseIdentity) -> dict[str, object]:
    # PostgreSQL fences are bigint; Lua doubles must not collapse adjacent values.
    return {**asdict(identity), "attempt_fence": str(identity.attempt_fence)}
