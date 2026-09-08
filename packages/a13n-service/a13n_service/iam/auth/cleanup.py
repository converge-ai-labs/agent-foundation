"""Bounded removal of expired, no-longer-usable email proofs."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from ..models import EmailChangeRecord, PasswordResetRecord


class IdentityTokenCleanup:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, batch_limit: int) -> None:
        self._sessions = sessions
        self._limit = batch_limit

    async def scan(self) -> Sweep:
        count = 0
        async with transaction(self._sessions) as session:
            for model in (PasswordResetRecord, EmailChangeRecord):
                ids = tuple(
                    await session.scalars(
                        select(model.id)
                        .where(model.expires_at < utc_now())
                        .order_by(model.expires_at, model.id)
                        .limit(self._limit)
                    )
                )
                if ids:
                    await session.execute(delete(model).where(model.id.in_(ids)))
                count += len(ids)
        return Sweep(examined=count, completed=count)
