"""Collect expired upload evidence independently of retained package bytes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import SkillUploadRecord


class SkillUploadRetention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, batch_limit: int) -> None:
        self._sessions = sessions
        self._batch_limit = batch_limit

    async def scan(self) -> Sweep:
        now = utc_now()
        async with transaction(self._sessions) as session:
            records = tuple(
                await session.scalars(
                    select(SkillUploadRecord)
                    .where(SkillUploadRecord.expires_at <= now)
                    .order_by(SkillUploadRecord.expires_at, SkillUploadRecord.id)
                    .limit(self._batch_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            age = max((now - assume_utc(row.expires_at)).total_seconds() for row in records) if records else None
            for record in records:
                await session.delete(record)
        return Sweep(examined=len(records), completed=len(records), oldest_age_seconds=age)
