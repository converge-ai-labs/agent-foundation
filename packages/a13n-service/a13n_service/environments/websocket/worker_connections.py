"""Worker incarnation mailbox and bounded Attempt-use lease renewal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError
from a13n_logging import get_logger
from anyio import move_on_after
from redis.asyncio import Redis

from a13n_service.iam.attempts import AttemptAuthorizationError
from a13n_service.iam.domain import AuthorizationError
from a13n_service.ids import new_object_id
from a13n_service.temporal import assume_utc, utc_now

from .authority import DispatchDenied, UseIdentity
from .coordination import ConnectionCoordination, CoordinationError
from .relay_client import RelayUseClient
from .relay_scope import RelayUseScope
from .relay_storage import ConnectionRelayStore, WorkerResponseMailbox
from .relay_waiters import RelayResponseDispatcher

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

logger = get_logger(__name__)


@dataclass(slots=True)
class _Use:
    scope: RelayUseScope
    attempt: AttemptContext
    clients: dict[str, RelayUseClient] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        identity = self.scope.identity
        return identity.connection.organization_id, identity.attempt_id, identity.connection.environment_id


@dataclass(slots=True)
class _Slot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: int = 0
    use: _Use | None = None


def _check_attempt(attempt: AttemptContext, environment_id: str) -> None:
    from a13n_service.interactions.attempts import AttemptAuthorityError

    try:
        attempt.lease.require_current(utc_now())
        attempt.authorization.require_environment(environment_id)
    except (AttemptAuthorityError, AttemptAuthorizationError, AuthorizationError) as error:
        raise DispatchDenied("The Attempt no longer authorizes Environment use") from error


class WorkerClientConnections:
    def __init__(
        self, redis: Redis, reader: Redis, instance_id: str, *, max_uses: int = 128, max_mounts: int = 32
    ) -> None:
        if not 1 <= max_uses <= 4096:
            raise ValueError("Worker client Environment use capacity must be bounded")
        if not 1 <= max_mounts <= 128:
            raise ValueError("Worker mounts per Environment use must be bounded")
        self.instance_id = instance_id
        self._redis = redis
        self._coordination = ConnectionCoordination(redis)
        self._mailbox = WorkerResponseMailbox(redis, instance_id, reader=reader)
        self._responses = RelayResponseDispatcher(self._mailbox)
        self._slots: dict[tuple[str, str, str], _Slot] = {}
        self._max_uses, self._max_mounts = max_uses, max_mounts
        self._prepared = False
        self._draining = False
        self._closed = asyncio.Event()

    async def prepare(self) -> None:
        if self._prepared or self._draining:
            raise RuntimeError("Worker client mailbox can be prepared exactly once")
        await self._mailbox.prepare()
        self._prepared = True

    @asynccontextmanager
    async def _slot(self, key: tuple[str, str, str]) -> AsyncIterator[_Slot]:
        slot = self._slots.get(key)
        if slot is None:
            if len(self._slots) >= self._max_uses:
                raise EnvironmentError("Worker Environment use capacity is exhausted", code="environment_overloaded")
            slot = self._slots[key] = _Slot()
        if slot.waiters >= self._max_mounts:
            raise EnvironmentError("Worker mount admission capacity is exhausted", code="environment_overloaded")
        slot.waiters += 1
        try:
            async with slot.lock:
                yield slot
        finally:
            slot.waiters -= 1
            self._discard_empty(key, slot)

    def _discard_empty(self, key: tuple[str, str, str], slot: _Slot) -> None:
        if slot.use is None and not slot.waiters and self._slots.get(key) is slot:
            del self._slots[key]

    async def acquire(
        self,
        attempt: AttemptContext,
        environment_id: str,
        permissions: frozenset[EnvironmentAction],
        *,
        mount_name: str = "workspace",
    ) -> RelayUseClient:
        key = attempt.organization_id, attempt.run_attempt_id, environment_id
        async with self._slot(key) as slot:
            if not self._prepared or self._draining or attempt.worker_id != self.instance_id:
                raise EnvironmentError("Client Environment Worker is unavailable", code="environment_unavailable")
            if slot.use is None:
                slot.use = await self._acquire_use(attempt, environment_id)
            use = slot.use
            try:
                if use.attempt != attempt or not use.scope.available:
                    raise EnvironmentError("Client Environment use is unavailable", code="environment_unavailable")
                if mount_name in use.clients:
                    raise EnvironmentError("The mount already owns a use client", code="environment_busy")
                if len(use.clients) >= self._max_mounts:
                    raise EnvironmentError("Worker mount capacity is exhausted", code="environment_overloaded")
                client = RelayUseClient(use.scope, permissions=permissions, mount_name=mount_name)
                use.clients[mount_name] = client
                return client
            except BaseException:
                if not use.clients:
                    await self._retire_locked(slot, use)
                raise

    async def _acquire_use(self, attempt: AttemptContext, environment_id: str) -> _Use:
        identity = None
        try:
            _check_attempt(attempt, environment_id)
            observation = await self._coordination.observe(attempt.organization_id, environment_id)
            if observation.value.status != "online" or observation.value.connection is None:
                raise EnvironmentError("Client Environment is not online", code="environment_unavailable")
            identity = UseIdentity(
                observation.value.connection,
                new_object_id("eu"),
                attempt.run_id,
                attempt.run_attempt_id,
                attempt.attempt_number,
                self.instance_id,
            )
            # A lost acquisition reply is resolved only with the same use identity.
            for retry in range(2):
                try:
                    _check_attempt(attempt, environment_id)
                    observation = await self._coordination.acquire_use(
                        identity, attempt_expires_at_ms=self._attempt_expiry(attempt)
                    )
                    break
                except CoordinationError as error:
                    if error.code != "coordination_unavailable" or retry:
                        raise
            if self._draining:
                raise DispatchDenied("Worker is draining")
            _check_attempt(attempt, environment_id)
            return _Use(
                RelayUseScope(
                    identity,
                    observation,
                    ConnectionRelayStore(self._redis, identity.connection),
                    self._responses,
                    check_authority=lambda: _check_attempt(attempt, environment_id),
                ),
                attempt,
            )
        except BaseException as error:
            if identity is not None:
                await self._release_identity(identity)
            if isinstance(error, CoordinationError):
                code = {
                    "environment_busy": "environment_busy",
                    "coordination_unavailable": "environment_coordination_unavailable",
                }.get(error.code, "environment_unavailable")
                raise EnvironmentError("Client Environment use could not be acquired", code=code) from error
            if isinstance(error, DispatchDenied):
                raise EnvironmentError(
                    "Client Environment use is unavailable", code="environment_unavailable"
                ) from error
            raise

    def stop_admission(self) -> None:
        self._draining = True

    def is_closed(self) -> bool:
        return self._closed.is_set()

    async def release(self, client: RelayUseClient) -> None:
        client.fence()
        identity = client.identity
        key = identity.connection.organization_id, identity.attempt_id, identity.connection.environment_id
        slot = self._slots.get(key)
        if slot is None:
            return
        async with slot.lock:
            use = slot.use
            if use is None or use.clients.get(client.mount_name) is not client:
                return
            del use.clients[client.mount_name]
            if not use.clients:
                await self._retire_locked(slot, use)

    async def _retire(self, use: _Use) -> None:
        slot = self._slots.get(use.key)
        if slot is None:
            return
        async with slot.lock:
            if slot.use is use:
                await self._retire_locked(slot, use)

    async def _retire_locked(self, slot: _Slot, use: _Use) -> None:
        try:
            # Only the last mount (or loss of shared authority) retires the carrier.
            await use.scope.invalidate()
        finally:
            try:
                await self._release_identity(use.scope.identity)
            finally:
                use.clients.clear()
                slot.use = None
                self._discard_empty(use.key, slot)

    async def _release_identity(self, identity: UseIdentity) -> None:
        with move_on_after(1.2, shield=True):
            try:
                await self._coordination.release_use(identity)
            except CoordinationError:
                # The socket owner observes release, or fences at the last grant.
                # Worker cannot acknowledge physical detachment on Control's behalf.
                pass

    @staticmethod
    def _attempt_expiry(attempt: AttemptContext) -> int:
        return int(assume_utc(attempt.lease.expires_at).timestamp() * 1000)

    async def _renew(self, use: _Use) -> None:
        scope, attempt = use.scope, use.attempt
        try:
            if not scope.available:
                raise DispatchDenied("Client Environment use is no longer available")
            observation = await self._coordination.renew_use(
                scope.identity, attempt_expires_at_ms=self._attempt_expiry(attempt)
            )
            await scope.renew(observation)
            return
        except CoordinationError as error:
            if error.code == "coordination_unavailable" and scope.available:
                return
        except DispatchDenied:
            pass
        await self._retire(use)
        logger.info(
            "client_environment_use_fenced",
            extra={
                "environment_id": scope.identity.connection.environment_id,
                "run_attempt_id": attempt.run_attempt_id,
            },
        )

    async def _maintain(self) -> None:
        next_touch = monotonic() + 30
        capacity = asyncio.Semaphore(16)

        async def renew(use: _Use) -> None:
            async with capacity:
                await self._renew(use)

        while not self._closed.is_set():
            async with asyncio.TaskGroup() as tasks:
                for slot in tuple(self._slots.values()):
                    if slot.use is not None:
                        tasks.create_task(renew(slot.use))
            if monotonic() >= next_touch:
                await self._mailbox.touch()
                next_touch = monotonic() + 30
            with move_on_after(self._coordination.limits.lease_ms / 3000):
                await self._closed.wait()

    async def run(self) -> None:
        if not self._prepared:
            raise RuntimeError("Worker client mailbox has not been prepared")
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._responses.run())
                tasks.create_task(self._maintain())
        finally:
            self._draining = True
            for slot in self._slots.values():
                if slot.use is not None:
                    slot.use.scope.fence()
            self._responses.close()

    async def close(self) -> None:
        self.stop_admission()
        try:
            async with asyncio.TaskGroup() as tasks:
                for slot in tuple(self._slots.values()):
                    if slot.use is not None:
                        tasks.create_task(self._retire(slot.use))
        finally:
            self._responses.close()
            self._closed.set()
