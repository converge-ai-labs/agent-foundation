"""Composition boundary between Run acceptance and inline Hook creation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import create_inline_hook_subscription, load_inline_hook_subscription

from .models import RunRecord


async def create_inline_run_hook(
    database: AsyncSession,
    *,
    run: RunRecord,
    workspace_id: str,
    subscription: InlineHookSubscriptionInput | None,
    now: datetime,
) -> str | None:
    if subscription is None:
        return None
    record = await create_inline_hook_subscription(
        database,
        organization_id=run.tenant_id,
        workspace_id=workspace_id,
        session_id=run.session_id,
        thread_id=run.thread_id,
        run_id=run.id,
        actor_type=run.authority_principal_type,
        actor_id=run.authority_principal_id,
        subscription=subscription,
        now=now,
    )
    return record.id


async def inline_run_hook_replay_matches(
    database: AsyncSession,
    *,
    run: RunRecord,
    expected: InlineHookSubscriptionInput | None,
) -> bool:
    persisted = await load_inline_hook_subscription(
        database,
        organization_id=run.tenant_id,
        run_id=run.id,
    )
    if persisted is None:
        return expected is None
    if expected is None:
        return False
    head, revision = persisted
    expected_configuration = expected.bind_run_scope(
        session_id=run.session_id,
        thread_id=run.thread_id,
        run_id=run.id,
    )
    return revision.configuration() == expected_configuration and head.id == revision.hook_subscription_id


__all__ = ["create_inline_run_hook", "inline_run_hook_replay_matches"]
