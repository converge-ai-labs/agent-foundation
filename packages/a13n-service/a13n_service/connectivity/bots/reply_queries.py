"""Authorized inspection of provider-confirmed reply outcomes under current history authority."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.native_management import authorize, require_limit
from a13n_service.connectivity.providers.lark.actions import LarkReplyReceipt
from a13n_service.connectivity.providers.slack.client import SlackReplyReceipt
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .models import BotReplyRecord


class BotReplyObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    test_id: str | None
    run_id: str
    run_attempt_id: str
    target_id: str | None
    provider_key: Literal["slack", "lark"]
    account_version: int
    credential_generation: int
    status: Literal["dispatching", "succeeded", "rejected", "outcome_unknown"]
    receipt: SlackReplyReceipt | LarkReplyReceipt | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None


class BotReplyCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[BotReplyObservation, ...]
    next_cursor: str | None


async def list_bot_replies(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    account_id: str,
    run_id: str,
    limit: int,
    cursor: str | None,
    clock: Clock = utc_now,
) -> BotReplyCollection:
    require_limit(limit)
    async with short_session(sessions) as database:
        account = await require_account(database, account_id)
        await authorize(database, actor, account.workspace_id, WorkspaceAction.application_account_read)
        run = await database.scalar(
            select(RunRecord)
            .join(
                SessionRecord,
                and_(
                    SessionRecord.organization_id == RunRecord.organization_id, SessionRecord.id == RunRecord.session_id
                ),
            )
            .where(
                RunRecord.id == run_id,
                RunRecord.organization_id == account.organization_id,
                SessionRecord.workspace_id == account.workspace_id,
            )
        )
        if run is None:
            raise NativeError("resource_not_found", "Run not found.", category=ErrorCategory.not_found)
        for action in (WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read):
            await authorize_agent(
                database, actor=actor, workspace_id=account.workspace_id, agent_id=run.agent_id, action=action
            )
        scope = {
            "kind": "bot_replies",
            "principal": actor.principal.model_dump(mode="json"),
            "workspace_id": account.workspace_id,
            "account_id": account_id,
            "run_id": run_id,
        }
        try:
            boundary = decode_cursor(cursor, scope=scope, id_prefix="brr") if cursor else None
        except CursorError as error:
            raise NativeError("invalid_cursor", str(error), category=ErrorCategory.invalid_request) from error
        query = (
            select(BotReplyRecord, RunAttemptRecord)
            .join(
                RunAttemptRecord,
                and_(
                    RunAttemptRecord.organization_id == BotReplyRecord.organization_id,
                    RunAttemptRecord.id == BotReplyRecord.run_attempt_id,
                ),
            )
            .where(
                BotReplyRecord.account_id == account_id,
                BotReplyRecord.run_id == run_id,
                BotReplyRecord.organization_id == account.organization_id,
                BotReplyRecord.workspace_id == account.workspace_id,
            )
            .order_by(BotReplyRecord.started_at.desc(), BotReplyRecord.id.desc())
            .limit(limit + 1)
        )
        if boundary:
            started_at, identity = boundary
            query = query.where(
                or_(
                    BotReplyRecord.started_at < started_at,
                    and_(BotReplyRecord.started_at == started_at, BotReplyRecord.id < identity),
                )
            )
        rows = (await database.execute(query)).all()
        now = clock()
        items = []
        for record, attempt in rows[:limit]:
            items.append(reply_observation(record, run, attempt, now))
    next_cursor = (
        encode_cursor(updated_at=items[-1].started_at, object_id=items[-1].id, scope=scope)
        if len(rows) > limit
        else None
    )
    return BotReplyCollection(items=tuple(items), next_cursor=next_cursor)


def reply_observation(
    record: BotReplyRecord, run: RunRecord, attempt: RunAttemptRecord, now: datetime
) -> BotReplyObservation:
    status = record.status
    if status == "dispatching" and not (
        run.status == "running"
        and run.current_run_attempt_id == attempt.id
        and attempt.status in {"leased", "running"}
        and assume_utc(attempt.lease_expires_at) > now
    ):
        status = "outcome_unknown"
    receipt_type = SlackReplyReceipt if record.provider_key == "slack" else LarkReplyReceipt
    return BotReplyObservation.model_validate(
        {
            **{
                name: getattr(record, name)
                for name in BotReplyObservation.model_fields
                if name not in {"status", "receipt"}
            },
            "status": status,
            "receipt": receipt_type.model_validate(record.receipt_json) if record.receipt_json else None,
        }
    )
