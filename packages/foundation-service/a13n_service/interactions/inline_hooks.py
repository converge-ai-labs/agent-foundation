"""Composition boundary between Run acceptance and inline Hook creation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.hooks import InlineHookValidationError, InlineHookValidator
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import create_inline_hook_subscription, load_inline_hook_subscription
from a13n_service.storage import short_session

from .domain import Run
from .errors import RunAcceptanceError
from .models import RunRecord
from .queue_persistence import QueueConsumptionConflict, load_live_queued_submission


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

    async def validate_queued_destination(
        self,
        *,
        tenant_id: str,
        queued_submission_id: str,
        submission_digest_sha256: str,
    ) -> None:
        async with short_session(self._sessions) as database:
            try:
                queued = await load_live_queued_submission(
                    database,
                    tenant_id=tenant_id,
                    queued_submission_id=queued_submission_id,
                    submission_digest_sha256=submission_digest_sha256,
                )
            except QueueConsumptionConflict as error:
                raise RunAcceptanceError(
                    "queue_consumption_conflict",
                    "Queued submission changed before Run acceptance",
                ) from error
        await self.validate_destination(queued.submission.hook_subscription)

    async def authorize(
        self,
        database: AsyncSession,
        *,
        run: Run,
        workspace_id: str,
        subscription: InlineHookSubscriptionInput | None,
    ) -> None:
        try:
            await self._validator.authorize(
                database,
                principal=run.authority_principal,
                organization_id=run.tenant_id,
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

    async def replay_matches(
        self,
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


__all__ = ["InlineHookAcceptance"]
