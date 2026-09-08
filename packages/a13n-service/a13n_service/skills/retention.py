"""Collect expired upload evidence independently of retained package bytes."""

from __future__ import annotations

from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import SkillUploadRecord


class SkillUploadRetention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, batch_limit: int) -> None:
        self._sessions = sessions
        self._batch_limit = batch_limit

    async def scan(self) -> Sweep:
        now = utc_now()
        # Receipts can embed the upload identity instead of using it as result_ref.
        replay = exists().where(
            IdempotencyEvidenceRecord.organization_id == SkillUploadRecord.organization_id,
            IdempotencyEvidenceRecord.expires_at > now,
            or_(
                IdempotencyEvidenceRecord.result_ref == SkillUploadRecord.id,
                IdempotencyEvidenceRecord.scope_id == SkillUploadRecord.id,
                cast(IdempotencyEvidenceRecord.receipt_json, String).contains(SkillUploadRecord.id),
            ),
        )
        async with transaction(self._sessions) as session:
            records = tuple(
                await session.scalars(
                    select(SkillUploadRecord)
                    .where(SkillUploadRecord.expires_at <= now, ~replay)
                    .order_by(SkillUploadRecord.expires_at, SkillUploadRecord.id)
                    .limit(self._batch_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            age = max((now - assume_utc(row.expires_at)).total_seconds() for row in records) if records else None
            for record in records:
                await session.delete(record)
        return Sweep(examined=len(records), completed=len(records), oldest_age_seconds=age)
