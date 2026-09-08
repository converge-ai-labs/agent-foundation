"""Bounded cleanup of terminal batches after their last deduplication deadline."""

import logging

import anyio
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .admission_models import IngressAdmissionRecord, IngressBatchRecord

logger = logging.getLogger("a13n_service.connectivity.ingress.retention")


class IngressRetentionReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: float = 60,
        batch_size: int = 25,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._clock = clock

    async def run(self) -> None:
        while True:
            removed = 0
            try:
                removed = await self.reconcile_once()
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception("ingress_retention_reconcile_failed")
            await anyio.sleep(0 if removed else self._poll_interval_seconds)

    async def reconcile_once(self) -> int:
        now = self._clock()
        async with transaction(self._sessions) as session:
            batch_ids = tuple(
                (
                    await session.scalars(
                        select(IngressBatchRecord.id)
                        .where(
                            IngressBatchRecord.status.in_(("accepted", "rejected")),
                            ~exists(
                                select(IngressAdmissionRecord.id).where(
                                    IngressAdmissionRecord.batch_id == IngressBatchRecord.id,
                                    IngressAdmissionRecord.dedup_expires_at > now,
                                )
                            ),
                        )
                        .order_by(IngressBatchRecord.terminal_at, IngressBatchRecord.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if batch_ids:
                await session.execute(
                    delete(IngressAdmissionRecord).where(IngressAdmissionRecord.batch_id.in_(batch_ids))
                )
                await session.execute(delete(IngressBatchRecord).where(IngressBatchRecord.id.in_(batch_ids)))
            rejected_ids = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord.id)
                        .where(
                            IngressAdmissionRecord.batch_id.is_(None), IngressAdmissionRecord.dedup_expires_at <= now
                        )
                        .order_by(IngressAdmissionRecord.dedup_expires_at, IngressAdmissionRecord.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if rejected_ids:
                await session.execute(delete(IngressAdmissionRecord).where(IngressAdmissionRecord.id.in_(rejected_ids)))
            return len(batch_ids) + len(rejected_ids)
