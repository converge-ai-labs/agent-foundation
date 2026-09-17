"""Attempt-fenced snapshots and observations for locally installed Run mounts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from a13n_harness import SafeFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, next_updated_at, utc_now

from .mount_domain import AcceptedRunMount
from .mount_models import RunEnvironmentMountRecord

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext


class RunMountObservations:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, clock: Clock = utc_now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def snapshot(self, attempt: AttemptContext) -> tuple[AcceptedRunMount, ...]:
        """Capture acceptance order under the same lock used by additions and Attempt transitions."""
        from a13n_service.interactions.attempts import lock_attempt_authority

        now = assume_utc(self._clock())
        attempt.lease.require_current(now)
        async with transaction(self._sessions) as session:
            await lock_attempt_authority(session, attempt, now)
            rows = await session.scalars(
                select(RunEnvironmentMountRecord)
                .where(
                    RunEnvironmentMountRecord.organization_id == attempt.organization_id,
                    RunEnvironmentMountRecord.run_id == attempt.run_id,
                )
                .order_by(RunEnvironmentMountRecord.created_at)
            )
            return tuple(AcceptedRunMount.from_record(row) for row in rows)

    async def validate(self, attempt: AttemptContext, mount: AcceptedRunMount) -> None:
        """Revalidate immediately before local publication, with no runtime work under the lock."""
        async with transaction(self._sessions) as session:
            await self._lock(session, attempt, mount, assume_utc(self._clock()))

    async def publish(
        self,
        attempt: AttemptContext,
        mount: AcceptedRunMount,
        status: Literal["preparing", "ready", "failed"],
        *,
        error: SafeFailure | None = None,
    ) -> None:
        """Record current-process evidence; a retry never re-enters the Environment adapter."""
        if error is not None and status != "failed":
            raise ValueError("Only failed mount observations can carry an error")
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as session:
            row = await self._lock(session, attempt, mount, now)
            row.applied_attempt_id = attempt.run_attempt_id
            row.applied_attempt_fence = attempt.attempt_number
            row.application_status = status
            row.observed_at = next_updated_at(row.observed_at, now) if row.observed_at is not None else now
            row.error = error.model_dump(mode="json") if error is not None else None

    async def _lock(
        self, session: AsyncSession, attempt: AttemptContext, mount: AcceptedRunMount, now: datetime
    ) -> RunEnvironmentMountRecord:
        from a13n_service.interactions.attempts import lock_attempt_authority

        attempt.lease.require_current(now)
        await lock_attempt_authority(session, attempt, now)
        if mount.run_id != attempt.run_id:
            raise ValueError("Mount does not belong to this Attempt's Run")
        row = await session.get(RunEnvironmentMountRecord, (mount.run_id, mount.name), with_for_update=True)
        if row is None or row.organization_id != attempt.organization_id or AcceptedRunMount.from_record(row) != mount:
            raise ValueError("The accepted Run mount is unavailable or changed")
        return row
