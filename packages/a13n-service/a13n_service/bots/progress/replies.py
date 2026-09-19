"""Explicit native replies update the same fenced task card as progress."""

import secrets
from datetime import timedelta

import anyio
import httpx2
from a13n_harness.providers.http import ProviderHttpError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.native_context import InboundRunContext, parse_native_contexts
from a13n_service.connectivity.providers.lark.actions import (
    LarkAutoReplyArguments,
    LarkPostContent,
    LarkReplyArguments,
    LarkReplyOutcome,
    LarkReplyOutcomeUnknown,
    LarkReplyReceipt,
    LarkReplySucceeded,
)
from a13n_service.connectivity.providers.lark.progress import task_card, validate_card
from a13n_service.connectivity.providers.slack.client import (
    SlackAutoReplyArguments,
    SlackForcedReplyArguments,
    SlackReplyArguments,
    SlackReplyOutcome,
    SlackReplyOutcomeUnknown,
    SlackReplyReceipt,
    SlackReplySucceeded,
)
from a13n_service.connectivity.providers.slack.progress import task_message
from a13n_service.iam import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .authority import ProgressUnavailable, authorize_progress
from .delivery import CardDelivery
from .models import ProgressRecord


class CardReplies:
    def __init__(self, delivery: CardDelivery) -> None:
        self.delivery = delivery

    async def reply(
        self,
        *,
        attempt: AttemptContext,
        context: InboundRunContext,
        arguments: LarkReplyArguments | SlackReplyArguments,
        account_version: int,
        credential_generation: int,
    ) -> LarkReplyOutcome | SlackReplyOutcome | None:
        # Older Runs and additional bound conversations retain their native transport.
        async with short_session(self.delivery.sessions) as session:
            row = await session.get(ProgressRecord, attempt.run_id)
            if row is None or not self._matches(row, context):
                return None
        request_id = new_object_id("reply")
        unknown = SlackReplyOutcomeUnknown if context.provider_key == "slack" else LarkReplyOutcomeUnknown
        lease = None
        deadline = anyio.current_time() + 45
        while lease is None:
            if anyio.current_time() >= deadline:
                raise ProviderHttpError("rate_limited", retry_after_seconds=2)
            lease = await self._claim(attempt, context, arguments, account_version, credential_generation)
            if lease is None:
                await anyio.sleep(0.25)
        try:
            message_id = await self.delivery.publish(attempt.run_id, lease)
        except ProviderHttpError as error:
            if error.code in {"provider_rejected", "rate_limited", "invalid_arguments", "endpoint_denied"}:
                # A definite rejection must not become a later successful background reply.
                await self._discard(attempt.run_id, lease)
                raise
            await self._retry(attempt.run_id, lease)
            return unknown(request_id=request_id)
        except (httpx2.HTTPError, TimeoutError, DBAPIError):
            await self._retry(attempt.run_id, lease)
            return unknown(request_id=request_id)
        except BaseException:
            # Cancellation cannot undo a committed intent or an uncertain provider write.
            await self._retry(attempt.run_id, lease)
            raise
        if message_id is None:
            return unknown(request_id=request_id)
        if context.provider_key == "slack":
            return SlackReplySucceeded(
                receipt=SlackReplyReceipt(
                    channel_id=str(context.provider_context["channel_id"]),
                    message_ts=message_id,
                    root_thread_ts=(
                        str(context.provider_context["root_thread_ts"])
                        if context.action_policy.get("reply_mode", "thread") == "thread"
                        or (
                            context.action_policy.get("reply_mode") == "auto"
                            and context.provider_context.get("conversation_kind") != "im"
                        )
                        else message_id
                    ),
                    request_id=request_id,
                )
            )
        return LarkReplySucceeded(receipt=LarkReplyReceipt(message_id=message_id, request_id=request_id))

    @staticmethod
    def _matches(row: ProgressRecord, context: InboundRunContext) -> bool:
        return (
            context.provider_key in {"lark", "slack"}
            and row.provider_key == context.provider_key
            and row.account_id == context.account_id
            and row.source_message_id
            == context.provider_context.get("root_thread_ts" if context.provider_key == "slack" else "message_id")
            and row.conversation_id
            == context.provider_context.get("channel_id" if context.provider_key == "slack" else "chat_id")
        )

    async def _claim(
        self,
        attempt: AttemptContext,
        context: InboundRunContext,
        arguments: LarkReplyArguments | SlackReplyArguments,
        account_version: int,
        credential_generation: int,
    ) -> str | None:
        now = utc_now()
        attempt.lease.require_current(now)
        async with transaction(self.delivery.sessions) as session:
            run, _, _ = await read_attempt_authority(session, attempt, now)
            account = await session.get(AccountRecord, context.account_id)
            if (
                account is None
                or account.status != "active"
                or account.deleted_at is not None
                or account.version != account_version
                or account.credential_generation != credential_generation
                or context not in parse_native_contexts(run.native_tool_contexts_json)
                or f"{context.provider_key}.reply" not in context.allowed_actions
            ):
                raise ProgressUnavailable("native_source_changed")
            row = await session.scalar(
                select(ProgressRecord).where(ProgressRecord.run_id == attempt.run_id).with_for_update()
            )
            if row is None or not self._matches(row, context) or row.account_version != account_version:
                raise ProgressUnavailable("task_card_unavailable")
            if row.lease_until is not None and assume_utc(row.lease_until) > now:
                return None
            placement = "thread" if row.reply_in_thread else "main"
            if isinstance(arguments, (LarkAutoReplyArguments, SlackAutoReplyArguments)) and arguments.placement not in {
                None,
                placement,
            }:
                raise ProviderHttpError("invalid_arguments")
            await authorize_progress(session, account, run, action=WorkspaceAction.run_read)
            if isinstance(arguments, (SlackAutoReplyArguments, SlackForcedReplyArguments)):
                text = arguments.text
            else:
                content = arguments.content
                text = (
                    "\n\n".join(part for part in (content.title, *content.paragraphs) if part)
                    if isinstance(content, LarkPostContent)
                    else content.text
                )
            replies = (*tuple(row.replies_json or ()), text)
            # Reserve room for the longest translated status, controls, and details URL.
            if context.provider_key == "slack":
                task_message(
                    status="running", run_id=row.run_id, token=row.action_token, details_url=None, replies=replies
                )
            else:
                validate_card(
                    task_card(
                        status="running", run_id=row.run_id, token=row.action_token, details_url=None, replies=replies
                    ),
                    reserve_bytes=4096,
                )
            lease = secrets.token_urlsafe(24)
            row.lease_token = lease
            row.lease_until = now + timedelta(seconds=60)
            row.replies_json = list(replies)
            row.done = False
            row.attempts = 0
            row.available_at = now
            return lease

    async def _retry(self, run_id: str, lease: str) -> None:
        with anyio.move_on_after(2, shield=True):
            try:
                await self.delivery.finish(run_id, lease, error="reply_outcome_unknown")
            except DBAPIError:
                pass  # Expiry allows the control worker to recover the persisted intent.

    async def _discard(self, run_id: str, lease: str) -> None:
        async with transaction(self.delivery.sessions) as session:
            row = await session.scalar(select(ProgressRecord).where(ProgressRecord.run_id == run_id).with_for_update())
            if row is not None and row.lease_token == lease:
                row.replies_json = (row.replies_json or [])[:-1]
                row.lease_token = None
                row.lease_until = None
                row.available_at = utc_now() + timedelta(seconds=5)
