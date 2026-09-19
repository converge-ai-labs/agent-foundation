"""Locally confirmed dispatch authority, independent of coordination I/O.

A renewal reply cannot revive expired authority. Fencing first rejects new writes,
then waits for the admitted frame write to settle before handover can acknowledge
the retirement. This gate surrounds transport writes, never operation results.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from a13n_service.iam.domain import PrincipalRef


@dataclass(frozen=True, slots=True)
class ConnectionIdentity:
    organization_id: str
    environment_id: str
    connection_id: str
    connection_epoch: str
    owner_instance_id: str


@dataclass(frozen=True, slots=True)
class UseIdentity:
    connection: ConnectionIdentity
    use_id: str
    run_id: str
    attempt_id: str
    attempt_fence: int
    worker_instance_id: str
    mount_name: str
    admission_deadline_ms: int = field(kw_only=True)
    kind: Literal["session"] = "session"

    @property
    def origin_instance_id(self) -> str:
        return self.worker_instance_id


@dataclass(frozen=True, slots=True)
class DeviceReadIdentity:
    connection: ConnectionIdentity
    principal: PrincipalRef
    origin_instance_id: str
    method: Literal["device.describe", "directory.list"]
    authorization_deadline_ms: int
    kind: Literal["device"] = "device"


type RequestIdentity = UseIdentity | DeviceReadIdentity


class DispatchDenied(Exception):
    """The exact local scope no longer permits a new transport write."""


@dataclass(frozen=True, slots=True)
class LeaseDeadline:
    """Conservative monotonic deadline calculated from a confirmed Redis grant."""

    monotonic_at: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.monotonic_at):
            raise ValueError("Lease deadline must be finite")

    @classmethod
    def confirmed(
        cls,
        *,
        request_started_at: float,
        server_now_ms: int,
        expires_at_ms: int,
        safety_margin_seconds: float,
    ) -> LeaseDeadline:
        if not math.isfinite(safety_margin_seconds) or safety_margin_seconds < 0:
            raise ValueError("Lease clock safety margin must be finite and non-negative")
        # Start at the request's send time, not its receipt time. Subtracting the
        # entire round trip is conservative even when the server clock differs.
        remaining = (expires_at_ms - server_now_ms) / 1000
        return cls(request_started_at + remaining - safety_margin_seconds)


class DispatchAuthority:
    """Irreversible local lease for one exact connection or Attempt/binding use scope.

    Connections use this during EIP initialization; use scopes additionally bind
    Run/Attempt/Worker identity. The caller publishes already-intersected access
    policy and rechecks it inside ``write`` before invoking the transport.
    """

    def __init__(
        self,
        identity: ConnectionIdentity | RequestIdentity,
        deadline: LeaseDeadline,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._identity = identity
        self._deadline = deadline.monotonic_at
        self._clock = clock
        self._fenced = False
        self._write_lock = asyncio.Lock()

    @property
    def identity(self) -> ConnectionIdentity | RequestIdentity:
        return self._identity

    @property
    def deadline(self) -> float:
        return self._deadline

    def check(self, identity: ConnectionIdentity | RequestIdentity) -> None:
        if self._clock() >= self._deadline:
            self._fenced = True
        if self._fenced or identity != self.identity:
            raise DispatchDenied("Client Environment dispatch authority is unavailable")

    def renew(self, identity: ConnectionIdentity | RequestIdentity, deadline: LeaseDeadline) -> None:
        """Accept only a still-live scope's confirmed grant; stale replies cannot revive it."""
        self.check(identity)
        if deadline.monotonic_at <= self._clock():
            raise DispatchDenied("Client Environment renewal arrived after its deadline")
        self._deadline = max(self._deadline, deadline.monotonic_at)

    @asynccontextmanager
    async def write(self, identity: ConnectionIdentity | RequestIdentity) -> AsyncIterator[None]:
        """Serialize a bounded frame write with fencing, without holding its result wait."""
        self.check(identity)
        async with asyncio.timeout(max(0, self._deadline - self._clock())):
            async with self._write_lock:
                self.check(identity)
                yield

    def invalidate(self) -> None:
        """Synchronously reject new writes, including during process drain."""
        self._fenced = True

    async def fence(self) -> None:
        """Reject admission immediately; return only after previously admitted writes settle."""
        self.invalidate()
        async with self._write_lock:
            pass
