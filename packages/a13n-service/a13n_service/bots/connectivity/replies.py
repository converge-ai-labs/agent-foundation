"""Durable native reply observations, independent of model output and Run success."""

import logging
from collections.abc import Awaitable, Callable
from typing import Literal

import anyio
from a13n_harness.providers.http import ProviderHttpError
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.bots.memory.settings import settings_version
from a13n_service.bots.progress.replies import CardReplies
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.ingress.admission_models import AgentThreadBindingRecord
from a13n_service.connectivity.native_context import InboundRunContext, NativeToolContext, parse_native_contexts
from a13n_service.connectivity.providers.github.actions import GitHubAddCommentOutcomeUnknown, GitHubAddCommentSucceeded
from a13n_service.connectivity.providers.lark.actions import (
    LarkAutoReplyArguments,
    LarkForcedReplyArguments,
    LarkReplyOutcomeUnknown,
    LarkReplySucceeded,
)
from a13n_service.connectivity.providers.slack.client import (
    SlackAutoReplyArguments,
    SlackForcedReplyArguments,
    SlackReplyOutcomeUnknown,
    SlackReplySucceeded,
)
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .models import BotReplyRecord, BotTestRecord
from .setup_tests import test_marker

logger = logging.getLogger("a13n_service.connectivity.bot_replies")
type ReplyStatus = Literal["dispatching", "succeeded", "rejected", "outcome_unknown"]
_DEFINITE_REJECTION = frozenset({"provider_rejected", "rate_limited", "invalid_arguments", "endpoint_denied"})


class BotReplyObserver:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        attempt: AttemptContext,
        context: InboundRunContext,
        workspace_id: str,
        account_version: int,
        credential_generation: int,
        clock: Clock = utc_now,
        cards: CardReplies | None = None,
    ) -> None:
        self._sessions = sessions
        self._attempt = attempt
        self._context = context
        self._workspace_id = workspace_id
        self._account_version = account_version
        self._generation = credential_generation
        self._clock = clock
        self._cards = cards

    async def __call__(
        self, invoke: Callable[[], Awaitable[BaseModel]], arguments: BaseModel | None = None
    ) -> BaseModel:
        identity = await self._begin(arguments)
        try:
            result = None
            if (
                self._cards is not None
                and self._context.provider_key in {"lark", "slack"}
                and isinstance(
                    arguments,
                    (
                        LarkAutoReplyArguments,
                        LarkForcedReplyArguments,
                        SlackAutoReplyArguments,
                        SlackForcedReplyArguments,
                    ),
                )
            ):
                result = await self._cards.reply(
                    attempt=self._attempt,
                    context=self._context,
                    arguments=arguments,
                    account_version=self._account_version,
                    credential_generation=self._generation,
                )
            if result is None:
                result = await invoke()
        except BaseException as error:
            code = error.code if isinstance(error, ProviderHttpError) and error.code in _DEFINITE_REJECTION else None
            await self._finish(identity, "rejected" if code else "outcome_unknown", None, code)
            raise
        success_type = (
            SlackReplySucceeded
            if self._context.provider_key == "slack"
            else GitHubAddCommentSucceeded
            if self._context.provider_key == "github"
            else LarkReplySucceeded
        )
        unknown_type = (
            SlackReplyOutcomeUnknown
            if self._context.provider_key == "slack"
            else GitHubAddCommentOutcomeUnknown
            if self._context.provider_key == "github"
            else LarkReplyOutcomeUnknown
        )
        if isinstance(result, success_type):
            await self._finish(identity, "succeeded", result.receipt.model_dump(mode="json"), None)
        else:
            await self._finish(
                identity,
                "outcome_unknown",
                None,
                None if isinstance(result, unknown_type) else "invalid_provider_outcome",
            )
        return result

    async def _begin(self, arguments: BaseModel | None) -> str:
        now = self._clock()
        identity = new_object_id("brr")
        context = self._context
        async with transaction(self._sessions) as database:
            run, _, _ = await read_attempt_authority(database, self._attempt, now)
            account = await require_account(database, context.account_id)
            if (
                context not in parse_native_contexts(run.native_tool_contexts_json)
                or account.organization_id != self._attempt.organization_id
                or account.workspace_id != self._workspace_id
                or account.provider_key != context.provider_key
                or account.provider_key not in {"slack", "lark", "github"}
                or account.status != "active"
                or account.version != self._account_version
                or account.credential_generation != self._generation
            ):
                raise ValueError("native_source_changed")
            binding = await database.scalar(
                select(AgentThreadBindingRecord.id).where(
                    AgentThreadBindingRecord.id == context.binding_id,
                    AgentThreadBindingRecord.account_id == context.account_id,
                    AgentThreadBindingRecord.organization_id == account.organization_id,
                    AgentThreadBindingRecord.workspace_id == account.workspace_id,
                )
            )
            if binding is None:
                raise ValueError("native_binding_unavailable")
            # Validated content is inspected transiently, never persisted in observations.
            values = arguments.model_dump(mode="json") if arguments is not None else {}
            text = values.get("body") if context.provider_key == "github" else values.get("text")
            content = values.get("content")
            if not isinstance(text, str) and isinstance(content, dict):
                text = content.get("text")
                if not isinstance(text, str):
                    parts = [content.get("title"), *content.get("paragraphs", [])]
                    text = "\n".join(part for part in parts if isinstance(part, str))
            marker = test_marker(text if isinstance(text, str) else None)
            test_id = None
            if marker is not None:
                test_id = await database.scalar(
                    select(BotTestRecord.id)
                    .join(AccountTargetRecord, AccountTargetRecord.id == BotTestRecord.target_id)
                    .where(
                        BotTestRecord.id == marker,
                        AccountTargetRecord.account_id == account.id,
                        AccountTargetRecord.version == BotTestRecord.target_version,
                        AccountTargetRecord.receive_enabled.is_(True),
                        BotTestRecord.account_id == account.id,
                        BotTestRecord.account_version == self._account_version,
                        BotTestRecord.settings_version == await settings_version(database, account.id),
                        BotTestRecord.credential_generation == self._generation,
                        BotTestRecord.binding_id == context.binding_id,
                        BotTestRecord.target_id == context.target_id,
                        BotTestRecord.accepted_at.is_not(None),
                        BotTestRecord.accepted_at <= now,
                    )
                )
            database.add(
                BotReplyRecord(
                    id=identity,
                    test_id=test_id,
                    organization_id=account.organization_id,
                    workspace_id=account.workspace_id,
                    account_id=account.id,
                    binding_id=context.binding_id,
                    target_id=context.target_id,
                    run_id=self._attempt.run_id,
                    run_attempt_id=self._attempt.run_attempt_id,
                    provider_key=context.provider_key,
                    account_version=self._account_version,
                    settings_version=await settings_version(database, account.id),
                    credential_generation=self._generation,
                    status="dispatching",
                    started_at=now,
                )
            )
        return identity

    async def _finish(
        self, identity: str, status: ReplyStatus, receipt: dict[str, object] | None, error_code: str | None
    ) -> None:
        # Recording a completed external effect is not a new authority grant.
        # Preserve evidence even if the Attempt lost its lease during provider I/O.
        # A failed commit must not turn a confirmed reply into a retryable tool error.
        try:
            with anyio.move_on_after(2, shield=True) as cancellation:
                async with transaction(self._sessions) as database:
                    await database.execute(
                        update(BotReplyRecord)
                        .where(BotReplyRecord.id == identity, BotReplyRecord.status == "dispatching")
                        .values(status=status, receipt_json=receipt, error_code=error_code, finished_at=self._clock())
                    )
            if not cancellation.cancel_called:
                return
        except Exception:
            pass
        logger.warning("bot_reply_observation_unconfirmed", extra={"reply_observation_id": identity})


class ReplyObservations:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, cards: CardReplies | None = None) -> None:
        self.sessions = sessions
        self.cards = cards

    def __call__(
        self,
        *,
        action: str,
        attempt: AttemptContext,
        context: NativeToolContext,
        workspace_id: str,
        account_version: int,
        credential_generation: int,
    ) -> BotReplyObserver | None:
        if not isinstance(context, InboundRunContext) or action not in {
            "slack.reply",
            "lark.reply",
            "github.add_comment",
        }:
            return None
        return BotReplyObserver(
            self.sessions,
            attempt=attempt,
            context=context,
            workspace_id=workspace_id,
            account_version=account_version,
            credential_generation=credential_generation,
            cards=self.cards,
        )
