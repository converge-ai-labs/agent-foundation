"""User-sent Bot setup tests with exact admission and reply correlation."""

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.bots.memory.settings import settings_version
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.admission_domain import BatchConfiguration
from a13n_service.connectivity.ingress.provider import InboundEvent
from a13n_service.connectivity.native_management import (
    audit,
    authorize,
    idempotency_key_digest,
    require_version,
)
from a13n_service.durable_operations.entity_keys import entity_key, find_by_key
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .models import BotReplyRecord, BotTestRecord
from .reply_queries import BotReplyObservation, reply_observation

_MARKER = re.compile(r"(?<![a-zA-Z0-9_])btest_[a-f0-9]{32}(?![a-zA-Z0-9_])")


class CreateBotTest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1)
    target_id: str = Field(min_length=1, max_length=72)
    target_version: int = Field(ge=1)


class BotTest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    account_id: str
    account_version: int
    credential_generation: int
    target_id: str
    target_version: int
    external_target_id: str
    created_at: datetime
    expires_at: datetime
    stale: bool
    event_received_at: datetime | None
    admission_id: str | None
    accepted_at: datetime | None
    run_id: str | None
    steer_id: str | None
    rejection_code: str | None
    reply: BotReplyObservation | None = None
    session_id: str | None = None
    thread_id: str | None = None


class BotTestHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    latest: BotTest | None


def test_marker(text: str | None) -> str | None:
    """Only an unambiguous marker counts; markers are correlation, never authority."""
    if text is None:
        return None
    matches = _MARKER.finditer(text)
    first = next(matches, None)
    return first.group() if first is not None and next(matches, None) is None else None


def _project(
    record: BotTestRecord, account: AccountRecord, target: AccountTargetRecord | None, current_settings_version: int
) -> BotTest:
    return BotTest(
        **{
            name: getattr(record, name)
            for name in BotTest.model_fields
            if name not in {"stale", "reply", "session_id", "thread_id"}
        },
        stale=(
            account.version != record.account_version
            or record.settings_version != current_settings_version
            or account.credential_generation != record.credential_generation
            or account.status != "active"
            or not account.receive_enabled
            or target is None
            or target.account_id != account.id
            or target.version != record.target_version
            or not target.receive_enabled
        ),
    )


async def create_bot_test(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    account_id: str,
    request: CreateBotTest,
    idempotency_key: str,
    clock: Clock = utc_now,
) -> BotTest:
    digest = idempotency_key_digest(idempotency_key)
    now = clock()
    async with transaction(sessions) as database:
        account = await require_account(database, account_id, lock=True)
        await authorize(database, actor, account.workspace_id, WorkspaceAction.application_account_manage)
        replay = await find_by_key(
            database,
            BotTestRecord,
            entity_key(
                actor,
                operation="bot_test.create",
                scope_id=account.id,
                key_digest=digest,
                workspace_id=account.workspace_id,
            ),
        )
        if replay is not None:
            return await _read_test(database, actor, account, replay, clock)
        require_version(account.version, request.expected_version)
        target = await database.get(AccountTargetRecord, request.target_id)
        if (
            target is None
            or target.account_id != account.id
            or target.target_kind != ("repository" if account.provider_key == "github" else "conversation")
        ):
            raise NativeError(
                "target_not_found", "The pilot conversation was not found.", category=ErrorCategory.not_found
            )
        require_version(target.version, request.target_version)
        if (
            account.provider_key not in {"slack", "lark", "github"}
            or account.status != "active"
            or not account.receive_enabled
            or not target.receive_enabled
        ):
            raise NativeError(
                "bot_reception_disabled",
                "Enable reception for the pilot conversation before testing.",
                category=ErrorCategory.conflict,
            )
        record = BotTestRecord(
            id=new_object_id("btest"),
            organization_id=account.organization_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            account_version=account.version,
            settings_version=await settings_version(database, account.id),
            credential_generation=account.credential_generation,
            target_id=target.id,
            target_version=target.version,
            external_target_id=target.external_target_id,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        record.request_key = entity_key(
            actor,
            operation="bot_test.create",
            scope_id=account.id,
            key_digest=digest,
            workspace_id=account.workspace_id,
        )
        database.add(record)
        await database.flush()
        resource = _project(record, account, target, await settings_version(database, account.id))
        database.add(audit(actor, account.organization_id, account.workspace_id, "bot_test.create", record.id, now))
        return resource


async def get_bot_test(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    account_id: str,
    test_id: str | None,
    clock: Clock = utc_now,
) -> BotTestHistory:
    async with short_session(sessions) as database:
        account = await require_account(database, account_id)
        await authorize(database, actor, account.workspace_id, WorkspaceAction.application_account_read)
        query = select(BotTestRecord).where(BotTestRecord.account_id == account_id)
        if test_id is not None:
            query = query.where(BotTestRecord.id == test_id)
        record = await database.scalar(
            query.order_by(BotTestRecord.created_at.desc(), BotTestRecord.id.desc()).limit(1)
        )
        if record is None:
            return BotTestHistory(latest=None)
        return BotTestHistory(latest=await _read_test(database, actor, account, record, clock))


async def _read_test(
    database: AsyncSession, actor: AuthenticatedActor, account: AccountRecord, record: BotTestRecord, clock: Clock
) -> BotTest:
    run = None
    if record.run_id is not None:
        run = await database.get(RunRecord, record.run_id)
        if run is None:
            raise NativeError(
                "resource_not_found", "Test execution is no longer available.", category=ErrorCategory.not_found
            )
        for action in (WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read):
            await authorize_agent(
                database, actor=actor, workspace_id=account.workspace_id, agent_id=run.agent_id, action=action
            )
    target = await database.get(AccountTargetRecord, record.target_id)
    reply_row = (
        await database.execute(
            select(BotReplyRecord, RunRecord, RunAttemptRecord)
            .join(RunRecord, RunRecord.id == BotReplyRecord.run_id)
            .join(RunAttemptRecord, RunAttemptRecord.id == BotReplyRecord.run_attempt_id)
            .where(BotReplyRecord.test_id == record.id, BotReplyRecord.account_id == account.id)
            .order_by(
                case((BotReplyRecord.status == "succeeded", 0), else_=1),
                BotReplyRecord.started_at.desc(),
                BotReplyRecord.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    reply = None
    if reply_row is not None:
        evidence, reply_run, attempt = reply_row
        for action in (WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read):
            await authorize_agent(
                database, actor=actor, workspace_id=account.workspace_id, agent_id=reply_run.agent_id, action=action
            )
        reply = reply_observation(evidence, reply_run, attempt, clock())
    return _project(record, account, target, await settings_version(database, account.id)).model_copy(
        update={
            "reply": reply,
            "session_id": run.session_id if run else None,
            "thread_id": run.thread_id if run else None,
        }
    )


async def record_test_admission(
    database: AsyncSession,
    *,
    account: AccountRecord,
    configuration: BatchConfiguration,
    event: InboundEvent,
    admission_id: str,
    batch_id: str,
    binding_id: str,
    now: datetime,
) -> None:
    marker = test_marker(event.text)
    if marker is None:
        return
    await database.execute(
        update(BotTestRecord)
        .where(
            BotTestRecord.id == marker,
            BotTestRecord.account_id == account.id,
            BotTestRecord.account_version == account.version,
            BotTestRecord.settings_version == await settings_version(database, account.id),
            BotTestRecord.credential_generation == account.credential_generation,
            BotTestRecord.target_id == configuration.target_id,
            BotTestRecord.target_version == configuration.target_version,
            BotTestRecord.external_target_id == configuration.external_target_id,
            BotTestRecord.event_received_at.is_(None),
            BotTestRecord.created_at <= event.received_at,
            BotTestRecord.expires_at > now,
        )
        .values(
            event_received_at=event.received_at, admission_id=admission_id, batch_id=batch_id, binding_id=binding_id
        )
    )


async def record_test_acceptance(
    database: AsyncSession, *, batch_id: str, run_id: str, steer_id: str | None, now: datetime
) -> None:
    await database.execute(
        update(BotTestRecord)
        .where(BotTestRecord.batch_id == batch_id, BotTestRecord.accepted_at.is_(None))
        .values(run_id=run_id, steer_id=steer_id, accepted_at=now)
    )


async def record_test_rejection(database: AsyncSession, *, batch_id: str, reason_code: str) -> None:
    await database.execute(
        update(BotTestRecord)
        .where(BotTestRecord.batch_id == batch_id, BotTestRecord.accepted_at.is_(None))
        .values(rejection_code=reason_code)
    )


async def record_test_ignored(
    database: AsyncSession,
    *,
    account: AccountRecord,
    event: InboundEvent,
    target_kind: str,
    external_target_id: str,
    reason_code: str,
    now: datetime,
) -> None:
    marker = test_marker(event.text)
    if marker is None or target_kind not in {"conversation", "repository"}:
        return
    target = await database.scalar(
        select(AccountTargetRecord).where(
            AccountTargetRecord.account_id == account.id,
            AccountTargetRecord.target_kind == target_kind,
            AccountTargetRecord.external_target_id == external_target_id,
        )
    )
    if target is None:
        return
    await database.execute(
        update(BotTestRecord)
        .where(
            BotTestRecord.id == marker,
            BotTestRecord.account_id == account.id,
            BotTestRecord.account_version == account.version,
            BotTestRecord.settings_version == await settings_version(database, account.id),
            BotTestRecord.credential_generation == account.credential_generation,
            BotTestRecord.target_id == target.id,
            BotTestRecord.target_version == target.version,
            BotTestRecord.external_target_id == external_target_id,
            BotTestRecord.event_received_at.is_(None),
            BotTestRecord.created_at <= event.received_at,
            BotTestRecord.expires_at > now,
        )
        .values(event_received_at=event.received_at, rejection_code=reason_code)
    )


class SetupObservations:
    ignored = staticmethod(record_test_ignored)
    admitted = staticmethod(record_test_admission)
    rejected = staticmethod(record_test_rejection)
