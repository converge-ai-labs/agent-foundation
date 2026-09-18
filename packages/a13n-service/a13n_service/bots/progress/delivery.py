"""Shared fenced delivery of a task card and explicitly authorized answers."""

import json
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

import anyio
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.connectivity.providers.lark.progress import LarkProgressClient, task_card
from a13n_service.connectivity.providers.slack.progress import SlackProgressClient, task_message
from a13n_service.iam import (
    AuthenticatedActor,
    WorkspaceAction,
)
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .authority import ProgressUnavailable, authorize_progress
from .domain import TaskProgress
from .models import ProgressRecord


@dataclass(frozen=True, repr=False)
class _Work:
    progress: TaskProgress
    provider_key: str
    conversation_id: str
    reply_in_thread: bool
    source_message_id: str
    message_id: str | None
    action_token: str
    rendered_status: str | None
    replies: tuple[str, ...]
    rendered_reply_count: int
    actor: AuthenticatedActor
    run_version: int
    thread_version: int
    configuration: JsonObject
    credentials: JsonObject


class CardDelivery:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx2.AsyncClient,
        endpoints: EndpointValidator,
        protector: SecretProtector,
        *,
        public_origin: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.http = http
        self.endpoints = endpoints
        self.protector = protector
        self.public_origin = public_origin

    async def load(self, run_id: str, lease: str) -> _Work | None:
        async with short_session(self.sessions) as session:
            row = await session.get(ProgressRecord, run_id)
            if (
                row is None
                or row.lease_token != lease
                or row.lease_until is None
                or assume_utc(row.lease_until) <= utc_now()
            ):
                return None
            account = await session.get(AccountRecord, row.account_id)
            run = await session.get(RunRecord, run_id)
            if (
                account is None
                or run is None
                or account.deleted_at is not None
                or account.status != "active"
                or account.version != row.account_version
            ):
                raise ProgressUnavailable("source_changed")
            actor, workspace_key = await authorize_progress(session, account, run, action=WorkspaceAction.run_read)
            thread = await session.get(ThreadRecord, run.thread_id)
            if thread is None:
                raise ProgressUnavailable("thread_unavailable")
            details_url = None
            if self.public_origin:
                details_url = f"{self.public_origin.rstrip('/')}/workspace/{quote(workspace_key, safe='')}/sessions/{quote(run.session_id, safe='')}/threads/{quote(run.thread_id, safe='')}/runs/{quote(run.id, safe='')}"
            credential = account.credential_snapshot()
            values = dict(account.provider_config_json)
            if (
                row.message_id is None
                and row.delivery_started_at is not None
                and utc_now() - assume_utc(row.delivery_started_at) > timedelta(minutes=50)
            ):
                raise ProgressUnavailable("initial_delivery_expired")
            credentials = json.loads(credential.decrypt(self.protector))
            return _Work(
                provider_key=row.provider_key,
                progress=TaskProgress(run.id, RunStatus(run.status), row.stop_requested, details_url),
                conversation_id=row.conversation_id,
                reply_in_thread=row.reply_in_thread,
                source_message_id=row.source_message_id,
                message_id=row.message_id,
                action_token=row.action_token,
                rendered_status=row.rendered_status,
                replies=tuple(row.replies_json or ()),
                rendered_reply_count=row.rendered_reply_count,
                actor=actor,
                run_version=run.version,
                thread_version=thread.version,
                configuration=values,
                credentials=credentials,
            )

    async def publish(self, run_id: str, lease: str) -> str | None:
        work = await self.load(run_id, lease)
        if work is None:
            return None
        status = work.progress.display_status
        if (
            work.rendered_status == status
            and work.message_id is not None
            and work.rendered_reply_count == len(work.replies)
        ):
            await self.finish(
                run_id,
                lease,
                status=status,
                message_id=work.message_id,
                done=work.progress.sealed,
                reply_count=len(work.replies),
            )
            return work.message_id
        client = (
            SlackProgressClient(self.http, token=str(work.credentials["bot_token"]))
            if work.provider_key == "slack"
            else LarkProgressClient(
                self.http,
                self.endpoints,
                origin=str(work.configuration["open_api_origin"]),
                app_id=str(work.configuration["app_id"]),
                secret=str(work.credentials["app_secret"]),
            )
        )

        def render(replies: tuple[str, ...]) -> JsonObject:
            if work.provider_key == "slack":
                return task_message(
                    status=status,
                    run_id=run_id,
                    token=work.action_token,
                    details_url=work.progress.details_url,
                    replies=replies,
                )
            return task_card(
                status=status,
                run_id=run_id,
                token=work.action_token,
                details_url=work.progress.details_url,
                language="zh_cn" if work.configuration.get("brand") == "feishu" else "en_us",
                replies=replies,
            )

        card = render(work.replies)
        async with transaction(self.sessions) as session:
            row = await session.get(ProgressRecord, run_id, with_for_update=True)
            if row is None or row.lease_token != lease:
                return None
            if row.provider_key == "slack" and row.message_id is None and row.delivery_started_at is not None:
                # Slack provides no documented durable postMessage deduplication contract.
                # Never repeat an initial write after an uncertain response or process crash.
                raise ConnectivityHttpError("provider_outcome_unknown")
            if row.delivery_started_at is None:
                row.delivery_started_at = utc_now()
        # The entire create/update sequence completes before the durable lease expires.
        try:
            with anyio.fail_after(20):
                message_id = await client.publish(
                    source_message_id=work.source_message_id,
                    conversation_id=work.conversation_id,
                    reply_in_thread=work.reply_in_thread,
                    message_id=work.message_id,
                    run_id=run_id,
                    card=card if work.message_id is not None else render(()),
                )
                if work.message_id is None:
                    async with transaction(self.sessions) as session:
                        row = await session.get(ProgressRecord, run_id, with_for_update=True)
                        if row is None or row.lease_token != lease:
                            return None
                        row.message_id = message_id
                    if work.replies:
                        # A deduplicated create can return an older status-only card after a lost response.
                        # Patch it before claiming that this explicit answer was delivered.
                        await client.publish(
                            source_message_id=work.source_message_id,
                            conversation_id=work.conversation_id,
                            reply_in_thread=work.reply_in_thread,
                            message_id=message_id,
                            run_id=run_id,
                            card=card,
                        )
        except ConnectivityHttpError as error:
            if work.provider_key == "slack" and error.code in {
                "provider_rejected",
                "rate_limited",
                "invalid_arguments",
                "endpoint_denied",
            }:
                async with transaction(self.sessions) as session:
                    row = await session.get(ProgressRecord, run_id, with_for_update=True)
                    if row is not None and row.lease_token == lease and row.message_id is None:
                        # A definite rejection confirms that the initial message was not created.
                        row.delivery_started_at = None
            raise
        await self.finish(
            run_id,
            lease,
            status=status,
            message_id=message_id,
            done=work.progress.sealed,
            reply_count=len(work.replies),
        )

        return message_id

    async def finish(
        self,
        run_id: str,
        lease: str,
        *,
        status: str | None = None,
        message_id: str | None = None,
        done: bool = False,
        reply_count: int = 0,
        error: str | None = None,
    ) -> None:
        async with transaction(self.sessions) as session:
            row = await session.scalar(select(ProgressRecord).where(ProgressRecord.run_id == run_id).with_for_update())
            if row is None or row.lease_token != lease:
                return
            row.lease_token = None
            row.lease_until = None
            row.last_error = error
            if error:
                row.attempts += 1
                row.done = row.attempts >= 8
                row.available_at = utc_now() + timedelta(seconds=min(60, 2**row.attempts))
            else:
                row.message_id = message_id
                row.rendered_status = status
                row.rendered_reply_count = reply_count
                row.done = done
                row.attempts = 0
                row.available_at = utc_now() + timedelta(seconds=5)
