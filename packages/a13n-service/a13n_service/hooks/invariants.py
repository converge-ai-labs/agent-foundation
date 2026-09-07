"""Shared persisted invariants for Hook subscriptions."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.secrets.models import SecretRecord

MAX_ACTIVE_HOOK_SUBSCRIPTIONS = 128


class HookSubscriptionInvariantCode(StrEnum):
    inline_conflict = "inline_conflict"
    subscription_limit = "subscription_limit"
    destination_limit = "destination_limit"
    secret_unavailable = "secret_unavailable"
    workspace_unavailable = "workspace_unavailable"
    event_workspace_unavailable = "event_workspace_unavailable"


class HookSubscriptionInvariantError(RuntimeError):
    """A persisted Hook subscription invariant would make source commits unsafe."""

    def __init__(self, code: HookSubscriptionInvariantCode, message: str) -> None:
        super().__init__(message)
        self.code = code


async def require_active_workspace_secret(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    secret_id: str,
) -> None:
    available = await database.scalar(
        select(SecretRecord.id).where(
            SecretRecord.id == secret_id,
            SecretRecord.organization_id == organization_id,
            SecretRecord.workspace_id == workspace_id,
            SecretRecord.owner_type == "workspace",
            SecretRecord.owner_id == workspace_id,
            SecretRecord.deleted_at.is_(None),
            SecretRecord.ciphertext.is_not(None),
        )
    )
    if available is None:
        raise HookSubscriptionInvariantError(
            HookSubscriptionInvariantCode.secret_unavailable,
            "the selected Hook signing Secret is unavailable",
        )


__all__ = [
    "MAX_ACTIVE_HOOK_SUBSCRIPTIONS",
    "HookSubscriptionInvariantCode",
    "HookSubscriptionInvariantError",
    "require_active_workspace_secret",
]
