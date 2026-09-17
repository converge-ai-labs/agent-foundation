"""Workspace Bot metadata projection; no provider calls or conversation-history grant."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.application_errors import ErrorCategory
from a13n_service.bots.memory.settings import AccountMemorySettings, AccountSettingsRecord
from a13n_service.connectivity.accounts.domain import Account
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.native_management import authorize, require_limit
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .domain import BotCheck
from .models import BotCheckRecord, BotReplyRecord, BotTestRecord

BotPlatform = Literal["slack", "lark", "github"]
BotSetupCondition = Literal["disabled", "needs_verification", "check_failed", "reception_off", "receiving"]
BotTestStage = Literal["waiting", "expired", "stale", "received", "rejected", "accepted", "reply_confirmed"]


class BotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: Account
    memory_settings: AccountMemorySettings
    external_organization_id: str | None
    external_organization_name: str | None
    setup_condition: BotSetupCondition
    configured_target_count: int = Field(ge=0)
    checked_at: datetime | None
    test_stage: BotTestStage | None
    test_observed_at: datetime | None


class BotCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[BotSummary, ...]
    next_cursor: str | None


async def list_bots(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    limit: int = 50,
    cursor: str | None = None,
    platform: BotPlatform | None = None,
    condition: BotSetupCondition | None = None,
    search: str | None = None,
    account_id: str | None = None,
) -> BotCollection:
    require_limit(limit)
    search = search.strip() if search else None
    search = search or None
    scope = {
        "kind": "bots",
        "account_id": account_id,
        "workspace_id": workspace_id,
        "actor": actor.principal.model_dump(mode="json"),
        "platform": platform,
        "condition": condition,
        "search": search,
    }
    try:
        position = decode_cursor(cursor, scope=scope, id_prefix="acct") if cursor else None
    except CursorError as error:
        raise NativeError(
            "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
        ) from error
    account, check = AccountRecord, BotCheckRecord
    # The installation row is generation-fenced; old credentials cannot lend their verified name or success.
    current_check = and_(
        check.account_id == account.id,
        check.conversation_id == "",
        check.credential_generation == account.credential_generation,
    )
    installation = check.result_json["installation"]
    setup_condition = case(
        (account.status == "disabled", "disabled"),
        (check.account_id.is_(None), "needs_verification"),
        (
            or_(
                check.result_json["error_code"].as_string().is_not(None),
                installation["enabled"].as_boolean().is_not(True),
            ),
            "check_failed",
        ),
        (account.receive_enabled.is_(False), "reception_off"),
        else_="receiving",
    )
    organization_name = installation["organization_name"].as_string()
    organization_id = func.coalesce(
        installation["organization_id"].as_string(),
        case(
            (account.provider_key == "slack", account.provider_config_json["team_id"].as_string()),
            (
                account.provider_key == "github",
                func.coalesce(
                    account.provider_config_json["installation_account_id"].as_string(),
                    account.provider_config_json["user_id"].as_string(),
                ),
            ),
            else_=account.provider_config_json["tenant_key"].as_string(),
        ),
    )
    target_count = (
        select(func.count())
        .where(
            AccountTargetRecord.account_id == account.id,
            AccountTargetRecord.target_kind.in_(("conversation", "repository")),
        )
        .correlate(account)
        .scalar_subquery()
    )
    latest_test_id = (
        select(BotTestRecord.id)
        .where(BotTestRecord.account_id == account.id)
        .order_by(BotTestRecord.created_at.desc(), BotTestRecord.id.desc())
        .limit(1)
        .correlate(account)
        .scalar_subquery()
    )
    test = aliased(BotTestRecord)
    target = aliased(AccountTargetRecord)
    reply_at = (
        select(func.max(BotReplyRecord.finished_at))
        .where(
            BotReplyRecord.test_id == test.id,
            BotReplyRecord.account_id == account.id,
            BotReplyRecord.status == "succeeded",
        )
        .correlate(account, test)
        .scalar_subquery()
    )
    async with transaction(sessions) as session:
        workspace = await authorize(session, actor, workspace_id, WorkspaceAction.application_account_read)
        query = (
            select(
                account,
                check,
                setup_condition,
                organization_id,
                organization_name,
                target_count,
                test,
                target.version,
                target.receive_enabled,
                reply_at,
            )
            .outerjoin(check, current_check)
            .outerjoin(test, test.id == latest_test_id)
            .outerjoin(target, and_(target.id == test.target_id, target.account_id == account.id))
            .where(
                account.workspace_id == workspace_id,
                account.organization_id == workspace.organization_id,
                account.deleted_at.is_(None),
                account.provider_key.in_(("slack", "lark", "github")),
            )
        )
        if account_id:
            query = query.where(account.id == account_id)
        if platform:
            query = query.where(account.provider_key == platform)
        if condition:
            query = query.where(setup_condition == condition)
        if search:
            # User wildcard characters are literal search text.
            query = query.where(
                or_(
                    account.name.icontains(search, autoescape=True),
                    organization_name.icontains(search, autoescape=True),
                    organization_id.icontains(search, autoescape=True),
                )
            )
        if position:
            query = query.where(
                or_(account.updated_at < position[0], and_(account.updated_at == position[0], account.id < position[1]))
            )
        rows = (
            await session.execute(query.order_by(account.updated_at.desc(), account.id.desc()).limit(limit + 1))
        ).all()
        settings_rows = {
            row.account_id: row
            for row in await session.scalars(
                select(AccountSettingsRecord).where(
                    AccountSettingsRecord.account_id.in_([row[0].id for row in rows[:limit]])
                )
            )
        }
        now = utc_now()
        items = []
        for (
            record,
            observation,
            state,
            org_id,
            org_name,
            count,
            probe,
            target_version,
            target_enabled,
            confirmed_at,
        ) in rows[:limit]:
            settings = settings_rows.get(record.id)
            memory_settings = AccountMemorySettings(
                account_id=record.id,
                version=settings.version if settings else 0,
                memory=settings.memory() if settings else None,
            )
            stage: BotTestStage | None = None
            observed_at = None
            if probe:
                observed_at = probe.event_received_at or probe.created_at
                if (
                    record.status != "active"
                    or not record.receive_enabled
                    or not target_enabled
                    or probe.settings_version != memory_settings.version
                    or probe.account_version != record.version
                    or probe.credential_generation != record.credential_generation
                    or probe.target_version != target_version
                ):
                    stage = "stale"
                elif confirmed_at:
                    stage, observed_at = "reply_confirmed", confirmed_at
                elif probe.rejection_code:
                    stage = "rejected"
                elif probe.accepted_at:
                    stage, observed_at = "accepted", probe.accepted_at
                elif probe.event_received_at:
                    stage = "received"
                else:
                    stage = "expired" if assume_utc(probe.expires_at) <= now else "waiting"
            checked_at = BotCheck.model_validate(observation.result_json).checked_at if observation else None
            items.append(
                BotSummary(
                    account=record.to_resource(),
                    memory_settings=memory_settings,
                    external_organization_id=org_id,
                    external_organization_name=org_name,
                    setup_condition=state,
                    configured_target_count=count,
                    checked_at=checked_at,
                    test_stage=stage,
                    test_observed_at=assume_utc(observed_at) if observed_at else None,
                )
            )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][0]
            next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
        return BotCollection(items=tuple(items), next_cursor=next_cursor)


async def get_bot_summary(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    account_id: str,
) -> BotSummary:
    async with transaction(sessions) as session:
        account = await require_account(session, account_id)
        workspace_id = account.workspace_id
    # The projection rechecks authority and liveness after this short lookup.
    collection = await list_bots(sessions, actor=actor, workspace_id=workspace_id, account_id=account_id, limit=1)
    if not collection.items:
        raise NativeError("account_not_found", "The Bot was not found.", category=ErrorCategory.not_found)
    return collection.items[0]
