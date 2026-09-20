"""Bounded retention for lifecycle facts and their durable deliveries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.background import PeriodicTask, Sweep
from a13n_service.durable_operations.idempotency import delete_expired_evidence
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.interactions.steer_idempotency import clear_expired_steer_idempotency
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc

from .domain import LifecycleProjectionState
from .models import LifecycleEventRecord


@dataclass(frozen=True, slots=True)
class LifecycleRetentionSweep:
    outbox_records_deleted: int
    lifecycle_events_deleted: int
    idempotency_evidence_deleted: int = 0
    steer_idempotency_cleared: int = 0


class LifecycleRetentionReconciler:
    """Release expired delivery evidence, then delete unpinned lifecycle facts."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        event_horizon: timedelta,
        published_delivery_horizon: timedelta,
        dead_letter_horizon: timedelta,
        poll_interval_seconds: float,
        batch_limit: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if min(event_horizon, published_delivery_horizon, dead_letter_horizon) <= timedelta(0):
            raise ValueError("Lifecycle and delivery retention horizons must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("Lifecycle retention poll interval must be positive")
        if batch_limit < 1 or batch_limit > 1000:
            raise ValueError("Lifecycle retention batch limit must be between 1 and 1000")
        self._sessions = sessions
        self._event_horizon = event_horizon
        self._published_delivery_horizon = published_delivery_horizon
        self._dead_letter_horizon = dead_letter_horizon
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_limit = batch_limit
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> None:
        await PeriodicTask(
            "evidence_lifecycle_retention",
            self.scan,
            interval_seconds=self._poll_interval_seconds,
            timeout_seconds=30,
        ).run()

    async def scan(self) -> Sweep:
        result = await self.reconcile_once()
        deleted = (
            result.outbox_records_deleted
            + result.lifecycle_events_deleted
            + result.idempotency_evidence_deleted
            + result.steer_idempotency_cleared
        )
        async with short_session(self._sessions) as database:
            oldest = await database.scalar(
                select(LifecycleEventRecord.created_at)
                .where(LifecycleEventRecord.created_at < self._clock() - self._event_horizon)
                .order_by(LifecycleEventRecord.created_at, LifecycleEventRecord.id)
                .limit(1)
            )
        return Sweep(
            examined=deleted + int(oldest is not None),
            completed=deleted,
            deferred=int(oldest is not None),
            oldest_age_seconds=((self._clock() - assume_utc(oldest)).total_seconds() if oldest is not None else None),
        )

    async def reconcile_once(self) -> LifecycleRetentionSweep:
        now = self._clock()
        async with transaction(self._sessions) as database:
            idempotency_evidence_deleted = await delete_expired_evidence(database, now=now, limit=self._batch_limit)
            steer_idempotency_cleared = await clear_expired_steer_idempotency(
                database, now=now, limit=self._batch_limit
            )
            outbox_records_deleted = await self._delete_expired_deliveries(database, now=now)
            lifecycle_events_deleted = await self._delete_expired_events(database, now=now)
        return LifecycleRetentionSweep(
            outbox_records_deleted, lifecycle_events_deleted, idempotency_evidence_deleted, steer_idempotency_cleared
        )

    async def _delete_expired_deliveries(self, database: AsyncSession, *, now: datetime) -> int:
        published_before = now - self._published_delivery_horizon
        dead_lettered_before = now - self._dead_letter_horizon
        records = (
            await database.scalars(
                select(OutboxRecord)
                .where(
                    or_(
                        (
                            (OutboxRecord.status == "published")
                            & (OutboxRecord.published_at < published_before)
                            & or_(
                                OutboxRecord.source_kind == "lifecycle_event",
                                (OutboxRecord.source_kind == "asset")
                                & (OutboxRecord.destination_kind == "asset_content_cleanup"),
                            )
                        ),
                        (
                            (OutboxRecord.status == "dead_lettered")
                            & (OutboxRecord.dead_lettered_at < dead_lettered_before)
                            & (OutboxRecord.source_kind == "lifecycle_event")
                        ),
                    ),
                )
                .order_by(OutboxRecord.updated_at, OutboxRecord.id)
                .limit(self._batch_limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for record in records:
            await database.delete(record)
        await database.flush()
        return len(records)

    async def _delete_expired_events(self, database: AsyncSession, *, now: datetime) -> int:
        event_cutoff = now - self._event_horizon
        settled_states = (
            LifecycleProjectionState.projected.value,
            LifecycleProjectionState.abandoned.value,
        )
        retained_delivery = exists().where(
            OutboxRecord.source_kind == "lifecycle_event",
            OutboxRecord.source_id == LifecycleEventRecord.id,
        )
        prior = aliased(LifecycleEventRecord)
        retained_prior_delivery = exists().where(
            OutboxRecord.source_kind == "lifecycle_event",
            OutboxRecord.source_id == prior.id,
        )
        blocking_prior = exists().where(
            prior.organization_id == LifecycleEventRecord.organization_id,
            prior.seq < LifecycleEventRecord.seq,
            or_(
                prior.created_at >= event_cutoff,
                prior.projection_state.not_in(settled_states),
                prior.hook_dispatch_state != "done",
                retained_prior_delivery,
            ),
        )
        # Do not skip locked rows: concurrent sweepers must serialize on the
        # oldest eligible fact or they could delete around a locked prefix.
        records = (
            await database.scalars(
                select(LifecycleEventRecord)
                .where(
                    LifecycleEventRecord.created_at < event_cutoff,
                    LifecycleEventRecord.projection_state.in_(settled_states),
                    LifecycleEventRecord.hook_dispatch_state == "done",
                    ~retained_delivery,
                    ~blocking_prior,
                )
                .order_by(LifecycleEventRecord.seq)
                .limit(self._batch_limit)
                .with_for_update()
            )
        ).all()
        for record in records:
            await database.delete(record)
        await database.flush()
        return len(records)


__all__ = ["LifecycleRetentionReconciler", "LifecycleRetentionSweep"]
