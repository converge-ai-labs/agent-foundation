"""Fence retained conversation scopes when their external target is removed."""

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.bots.models import BotCheckRecord

from .models import ScopeRecord


async def invalidate_conversation(session: AsyncSession, account_id: str, external_id: str) -> None:
    # The caller holds the Account lock shared with target and scope configuration.
    await session.execute(
        update(ScopeRecord)
        .where(
            ScopeRecord.account_id == account_id,
            ScopeRecord.external_conversation_id == external_id,
        )
        .values(audience="unknown", version=ScopeRecord.version + 1)
    )
    await session.execute(
        delete(BotCheckRecord).where(
            BotCheckRecord.account_id == account_id, BotCheckRecord.conversation_id == external_id
        )
    )
