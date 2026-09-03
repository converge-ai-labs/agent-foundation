"""Retrying projection of durable lifecycle facts into Run presentation persistence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import anyio
from a13n_harness import SafeFailure
from anyio import create_task_group
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.lifecycle import (
    LifecycleEvent,
    LifecycleProjectionClaim,
    claim_lifecycle_projections,
    complete_lifecycle_projection,
    fail_lifecycle_projection,
)
from a13n_service.storage import transaction

from .domain import RetainedReplayUnavailable, RunStreamEvent, deterministic_run_stream_event_id
from .redis import RedisRunStream
from .replay import RunReplayStore

logger = logging.getLogger("a13n_service.run_stream.projector")
_TERMINAL_RUN_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})


class LifecycleRunStreamProjector:
    """Claim durable facts briefly, project outside SQL, then settle their leases."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        stream: RedisRunStream,
        replay: RunReplayStore,
        *,
        worker_id: str,
        lease_duration: timedelta = timedelta(seconds=30),
        retry_after: timedelta = timedelta(seconds=5),
        max_attempts: int = 20,
        poll_interval_seconds: float = 1,
        claim_limit: int = 16,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not worker_id:
            raise ValueError("lifecycle projection worker identity is required")
        if lease_duration <= timedelta(0) or retry_after < timedelta(0) or max_attempts < 1:
            raise ValueError("lifecycle projection retry policy is invalid")
        if poll_interval_seconds <= 0 or claim_limit < 1 or claim_limit > 200:
            raise ValueError("lifecycle projection polling policy is invalid")
        self._sessions = sessions
        self._stream = stream
        self._replay = replay
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._retry_after = retry_after
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds
        self._claim_limit = claim_limit
        self._clock = clock

    async def run(self) -> None:
        while True:
            try:
                claimed = await self.project_once(limit=self._claim_limit)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception(
                    "Run Stream projection sweep failed",
                    extra={"event": "run_stream_projection_sweep_failed", "worker_id": self._worker_id},
                )
                claimed = 0
            if claimed < self._claim_limit:
                await anyio.sleep(self._poll_interval_seconds)

    async def project_once(self, *, limit: int = 16) -> int:
        now = _utc(self._clock())
        async with transaction(self._sessions) as database:
            claims = await claim_lifecycle_projections(
                database,
                lease_owner=self._worker_id,
                now=now,
                lease_duration=self._lease_duration,
                limit=limit,
            )
        async with create_task_group() as tasks:
            for claim in claims:
                tasks.start_soon(self._project_claim, claim)
        return len(claims)

    async def _project_claim(self, claim: LifecycleProjectionClaim) -> None:
        try:
            await self._project_event(claim.event)
        except Exception:
            logger.exception("Run Stream lifecycle projection failed", extra={"lifecycle_event_id": claim.event.id})
            async with transaction(self._sessions) as database:
                await fail_lifecycle_projection(
                    database,
                    claim,
                    failed_at=_utc(self._clock()),
                    retry_after=self._retry_after,
                    max_attempts=self._max_attempts,
                    failure=SafeFailure(
                        code="run_stream_projection_failed",
                        message="Run presentation projection is temporarily unavailable.",
                        retry_hint="dependency_change",
                    ),
                )
            return
        async with transaction(self._sessions) as database:
            await complete_lifecycle_projection(database, claim, projected_at=_utc(self._clock()))

    async def _project_event(self, event: LifecycleEvent) -> None:
        if event.thread_id is None:
            raise ValueError("Run lifecycle projection requires Thread correlation")
        await self._stream.append(
            event.tenant_id,
            RunStreamEvent(
                event_id=deterministic_run_stream_event_id("lifecycle", event.id),
                event_type=event.event_type,
                run_id=event.run_id,
                thread_id=event.thread_id,
                run_attempt_id=event.run_attempt_id,
                lifecycle_event_id=event.id,
                occurred_at=event.occurred_at,
                payload={
                    "resource_type": event.entity_type.value,
                    "resource_id": event.entity_id,
                    "resource_seq": event.resource_seq,
                    "resource_version": event.entity_version,
                    "schema_version": event.schema_version,
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "data": event.payload,
                },
            ),
        )
        if event.event_type not in _TERMINAL_RUN_EVENTS:
            return
        await self._stream.close(event.tenant_id, event.run_id, closed_at=event.occurred_at)
        try:
            source = await self._stream.complete_source(event.tenant_id, event.run_id)
            await self._replay.publish(event.tenant_id, event.run_id, source)
        except RetainedReplayUnavailable:
            logger.info(
                "Run Stream closed without retained replay",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("projection clock must return an offset-aware timestamp")
    return value.astimezone(UTC)


__all__ = ["LifecycleRunStreamProjector"]
