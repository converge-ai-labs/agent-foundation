"""Finite Device-only reads over the existing connection relay."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import JsonValue
from redis.asyncio import Redis

from a13n_service.ids import new_object_id

from ..devices import DeviceTarget, unavailable
from .authority import DeviceReadIdentity, DispatchAuthority, DispatchDenied, LeaseDeadline
from .coordination import ConnectionCoordination, CoordinationError
from .relay_protocol import RelayRequest
from .relay_runtime import RelayResponseRuntime
from .relay_storage import ConnectionRelayStore


class DeviceReadClient:
    def __init__(self, redis: Redis, runtime: RelayResponseRuntime) -> None:
        self._redis = redis
        self._runtime = runtime
        self._coordination = ConnectionCoordination(redis)

    async def __call__(
        self,
        target: DeviceTarget,
        method: Literal["device.describe", "directory.list"],
        payload: dict[str, JsonValue],
    ) -> JsonValue:
        try:
            # The caller captured this target in an authorized short SQL scope.
            # There is no durable use, Run or Session for this read.
            async with asyncio.timeout(10):
                observed = await self._coordination.observe(target.organization_id, target.environment_id)
                connection = observed.value.connection
                if observed.value.status != "online" or connection is None:
                    raise unavailable()
                expires_at_ms = observed.value.now_ms + 10_000
                identity = DeviceReadIdentity(
                    connection, target.principal, self._runtime.instance_id, method, expires_at_ms
                )
                deadline = LeaseDeadline.confirmed(
                    request_started_at=observed.request_started_at,
                    server_now_ms=observed.value.now_ms,
                    expires_at_ms=expires_at_ms,
                    safety_margin_seconds=observed.safety_margin_seconds,
                )
                authority = DispatchAuthority(identity, deadline)
                request = RelayRequest(
                    request_id=new_object_id("erq"),
                    scope=identity,
                    operation=method,
                    deadline_ms=expires_at_ms,
                    payload=payload,
                )
                store = ConnectionRelayStore(self._redis, connection)
                with self._runtime.responses.register(request, authority, deadline) as pending:
                    async with asyncio.timeout_at(deadline.monotonic_at):
                        await pending.publish(store)
                        return (await pending.result()).result
        except (CoordinationError, DispatchDenied, TimeoutError) as error:
            raise unavailable() from error
