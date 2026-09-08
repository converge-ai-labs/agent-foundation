"""Exact Account lookup; callers enforce operation authority."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.errors import NativeError

from .models import AccountRecord


async def require_account(session: AsyncSession, account_id: str, *, lock: bool = False) -> AccountRecord:
    query = select(AccountRecord).where(AccountRecord.id == account_id, AccountRecord.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise NativeError(
            "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
        )
    return record
