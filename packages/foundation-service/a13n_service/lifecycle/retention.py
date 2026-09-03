"""Bounded retention for lifecycle facts and their durable deliveries."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.storage import transaction

from .domain import LifecycleProjectionState
from .models import LifecycleEventRecord

logger = logging.getLogger("a13n_service.lifecycle.retention")


@dataclass(frozen=True, slots=True)
class LifecycleRetentionSweep:
    outbox_records_deleted: int
    lifecycle_events_deleted: int


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
        while True:
            try:
                sweep = await self.reconcile_once()
                if sweep.outbox_records_deleted or sweep.lifecycle_events_deleted:
                    logger.info(
                        "lifecycle_retention_sweep_completed",
                        extra={
                            "event": "lifecycle_retention_sweep_completed",
                            "outbox_records_deleted": sweep.outbox_records_deleted,
                            "lifecycle_events_deleted": sweep.lifecycle_events_deleted,
                        },
                    )
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception(
                    "lifecycle_retention_sweep_failed",
                    extra={"event": "lifecycle_retention_sweep_failed"},
                )
            await anyio.sleep(self._poll_interval_seconds)

    async def reconcile_once(self) -> LifecycleRetentionSweep:
        now = self._clock()
        async with transaction(self._sessions) as database:
            outbox_records_deleted = await self._delete_expired_deliveries(database, now=now)
            lifecycle_events_deleted = await self._delete_expired_events(database, now=now)
        return LifecycleRetentionSweep(outbox_records_deleted, lifecycle_events_deleted)

    async def _delete_expired_deliveries(self, database: AsyncSession, *, now: datetime) -> int:
        published_before = now - self._published_delivery_horizon
        dead_lettered_before = now - self._dead_letter_horizon
        records = (
            await database.scalars(
                select(OutboxRecord)
                .where(
                    OutboxRecord.source_kind == "lifecycle_event",
                    or_(
                        ((OutboxRecord.status == "published") & (OutboxRecord.published_at < published_before)),
                        (
                            (OutboxRecord.status == "dead_lettered")
                            & (OutboxRecord.dead_lettered_at < dead_lettered_before)
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
            prior.tenant_id == LifecycleEventRecord.tenant_id,
            prior.entity_type == LifecycleEventRecord.entity_type,
            prior.entity_id == LifecycleEventRecord.entity_id,
            prior.resource_seq < LifecycleEventRecord.resource_seq,
            or_(
                prior.created_at >= event_cutoff,
                prior.projection_state.not_in(settled_states),
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
