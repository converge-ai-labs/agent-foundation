"""Resolve Account-owned conversation memory during trusted ingress acceptance."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.accounts.models import AccountRecord

from .binding import BotMemoryBinding
from .domain import ScopeSettings
from .models import ScopeRecord
from .settings import read_settings


async def select_binding(
    session: AsyncSession, account: AccountRecord, external_conversation_id: str, *, lock: bool = False
) -> BotMemoryBinding | None:
    if account.provider_key not in {"slack", "lark"}:
        return None
    disabled = BotMemoryBinding(account_id=account.id, external_conversation_id=external_conversation_id)
    settings = (await read_settings(session, account.id)).memory
    if settings is None:
        return disabled
    query = select(ScopeRecord).where(
        ScopeRecord.account_id == account.id,
        ScopeRecord.provider_id == settings.provider_id,
        ScopeRecord.external_conversation_id == external_conversation_id,
    )
    scope = await session.scalar(query.with_for_update() if lock else query)
    if scope is None:
        return disabled.model_copy(update={"provider_id": settings.provider_id})
    group = ScopeSettings.model_validate(scope.settings_json)
    eligible = group.enabled and scope.audience != "unknown"
    return BotMemoryBinding(
        account_id=account.id,
        external_conversation_id=external_conversation_id,
        provider_id=settings.provider_id,
        scope_id=scope.id,
        scope_version=scope.version,
        use_memory=eligible and settings.use_memory and group.use_memory,
        save_on_request=eligible and settings.save_on_request and group.save_on_request,
    )
