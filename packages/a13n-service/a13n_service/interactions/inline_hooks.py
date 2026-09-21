"""Composition boundary between Run acceptance and inline Hook creation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.hooks import InlineHookValidationError, InlineHookValidator
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import (
    create_inline_hook_subscription,
    load_inline_hook_subscription,
    lock_hook_workspace,
)
from a13n_service.iam import PrincipalRef
from a13n_service.storage import short_session

from .domain import Run
from .errors import RunAcceptanceError
from .models import RunRecord


class InlineHookAcceptance:
    """One composition boundary for inline Hook preflight and atomic acceptance."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        validator: InlineHookValidator,
    ) -> None:
        self._sessions = sessions
        self._validator = validator

    async def validate_destination(self, subscription: InlineHookSubscriptionInput | None) -> None:
        try:
            await self._validator.validate_destination(subscription)
        except InlineHookValidationError as error:
            raise RunAcceptanceError(error.code, str(error)) from error

    async def prepare(
        self,
        *,
        run: Run,
        subscription: InlineHookSubscriptionInput | None,
        source_run_id: str | None,
    ) -> InlineHookSubscriptionInput | None:
        if source_run_id is not None:
            async with short_session(self._sessions) as database:
                subscription = await self._inherit(database, run.organization_id, source_run_id)
        await self.validate_destination(subscription)
        return subscription

    async def _inherit(
        self,
        database: AsyncSession,
        organization_id: str,
        run_id: str,
        *,
        lock: bool = False,
    ) -> InlineHookSubscriptionInput | None:
        persisted = await load_inline_hook_subscription(
            database,
            organization_id=organization_id,
            run_id=run_id,
            lock=lock,
        )
        if persisted is None:
            return None
        head, revision = persisted
        if not head.enabled or head.deleted_at is not None:
            return None
        return InlineHookSubscriptionInput(
            hook_names=tuple(revision.hook_names),
            webhook=revision.to_resource().webhook,
        )

    async def authorize(
        self,
        database: AsyncSession,
        *,
        run: Run,
        workspace_id: str,
        subscription: InlineHookSubscriptionInput | None,
        source_run_id: str | None = None,
        actor: PrincipalRef | None = None,
    ) -> None:
        if source_run_id is not None:
            await lock_hook_workspace(database, organization_id=run.organization_id, workspace_id=workspace_id)
            current = await self._inherit(database, run.organization_id, source_run_id, lock=True)
            if current != subscription:
                raise RunAcceptanceError(
                    "inline_hook_source_changed",
                    "The source inline Hook changed before Run acceptance",
                )
        try:
            if source_run_id is None and actor is not None and actor != run.authority_principal:
                await self._validator.authorize(
                    database,
                    principal=actor,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    agent_id=run.agent_id,
                    subscription=subscription,
                )
            await self._validator.authorize(
                database,
                principal=run.authority_principal,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                agent_id=run.agent_id,
                subscription=subscription,
            )
        except InlineHookValidationError as error:
            raise RunAcceptanceError(error.code, str(error)) from error

    async def create(
        self,
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
            organization_id=run.organization_id,
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


__all__ = ["InlineHookAcceptance"]
