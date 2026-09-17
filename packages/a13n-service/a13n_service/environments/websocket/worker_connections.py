"""Worker incarnation mailbox and bounded Attempt-use lease renewal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from a13n_environment import EnvironmentAction, EnvironmentError
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
from .relay_storage import ConnectionRelayStore, WorkerResponseMailbox
from .relay_waiters import RelayResponseDispatcher

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Use:
    client: RelayUseClient
    attempt: AttemptContext


def _check_attempt(attempt: AttemptContext, environment_id: str) -> None:
    from a13n_service.interactions.attempts import AttemptAuthorityError

    try:
        attempt.lease.require_current(utc_now())
        attempt.authorization.require_environment(environment_id)
    except (AttemptAuthorityError, AttemptAuthorizationError, AuthorizationError) as error:
        raise DispatchDenied("The Attempt no longer authorizes Environment use") from error


class WorkerClientConnections:
    def __init__(self, redis: Redis, reader: Redis, instance_id: str, *, max_uses: int = 128) -> None:
        if not 1 <= max_uses <= 4096:
            raise ValueError("Worker client Environment use capacity must be bounded")
        self.instance_id = instance_id
        self._redis = redis
        self._coordination = ConnectionCoordination(redis)
        self._mailbox = WorkerResponseMailbox(redis, instance_id, reader=reader)
        self._responses = RelayResponseDispatcher(self._mailbox)
        self._uses: dict[str, _Use] = {}
        self._max_uses = max_uses
        self._opening = 0
        self._prepared = False
        self._draining = False
        self._closed = asyncio.Event()

    async def prepare(self) -> None:
        if self._prepared or self._draining:
            raise RuntimeError("Worker client mailbox can be prepared exactly once")
        await self._mailbox.prepare()
        self._prepared = True

    async def acquire(
        self, attempt: AttemptContext, environment_id: str, permissions: frozenset[EnvironmentAction]
    ) -> RelayUseClient:
        if not self._prepared or self._draining or attempt.worker_id != self.instance_id:
            raise EnvironmentError("Client Environment Worker is unavailable", code="environment_unavailable")
        if len(self._uses) + self._opening >= self._max_uses:
            raise EnvironmentError("Worker Environment use capacity is exhausted", code="environment_overloaded")
        identity = None
        self._opening += 1
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
            client = RelayUseClient(
                identity,
                observation,
                ConnectionRelayStore(self._redis, identity.connection),
                self._responses,
                check_authority=lambda: _check_attempt(attempt, environment_id),
                permissions=permissions,
            )
            self._uses[identity.use_id] = _Use(client, attempt)
            return client
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
        finally:
            self._opening -= 1

    def stop_admission(self) -> None:
        self._draining = True

    def is_closed(self) -> bool:
        return self._closed.is_set()

    async def release(self, client: RelayUseClient) -> None:
        owned = self._uses.get(client.identity.use_id)
        if owned is None:
            return
        if owned.client is not client:
            raise ValueError("Worker cannot release another use client")
        try:
            # Fence publications before retiring shared authority. Closing a use
            # does not depend on the response reader or another operation waiter.
            await client.invalidate()
        finally:
            try:
                await self._release_identity(client.identity)
            finally:
                self._uses.pop(client.identity.use_id, None)

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
        client, attempt = use.client, use.attempt
        try:
            if not client.available:
                raise DispatchDenied("Client Environment use is no longer available")
            observation = await self._coordination.renew_use(
                client.identity, attempt_expires_at_ms=self._attempt_expiry(attempt)
            )
            await client.renew(observation)
            return
        except CoordinationError as error:
            if error.code == "coordination_unavailable" and client.available:
                return
        except DispatchDenied:
            pass
        await self.release(client)
        logger.info(
            "client_environment_use_fenced",
            extra={
                "environment_id": client.identity.connection.environment_id,
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
                for use in tuple(self._uses.values()):
                    tasks.create_task(renew(use))
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
            for use in self._uses.values():
                use.client.fence()
            self._responses.close()

    async def close(self) -> None:
        self.stop_admission()
        try:
            async with asyncio.TaskGroup() as tasks:
                for use in tuple(self._uses.values()):
                    tasks.create_task(self.release(use.client))
        finally:
            self._responses.close()
            self._closed.set()
