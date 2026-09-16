"""Administrator-owned group-sharing policies and current participant intervals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.ids import new_object_id
from a13n_service.memory.service import failure
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .audit import audit
from .domain import (
    ReplaceSharingPolicy,
    ScopeSettings,
    SharingPolicy,
    SharingPolicyCollection,
    SharingPolicyInput,
)
from .models import ScopeRecord, SharingParticipantRecord, SharingPolicyRecord
from .service import require_version
from .settings import read_settings

if TYPE_CHECKING:
    from .service import BotMemoryService


async def eligible_groups(session: AsyncSession, account_id: str, provider_id: str, scope_ids: tuple[str, ...]) -> None:
    if len(set(scope_ids)) != len(scope_ids):
        raise failure("duplicate_group", "Select each group once.", ErrorCategory.invalid_request)
    groups = list(await session.scalars(select(ScopeRecord).where(ScopeRecord.id.in_(scope_ids))))
    if len(groups) != len(scope_ids) or any(
        group.account_id != account_id
        or group.provider_id != provider_id
        or group.audience not in ("public", "private")
        or not ScopeSettings.model_validate(group.settings_json).enabled
        for group in groups
    ):
        raise failure(
            "sharing_group_unavailable", "A selected group is not eligible for sharing.", ErrorCategory.conflict
        )


async def _account(
    session: AsyncSession, actor: AuthenticatedActor, account_id: str, *, lock: bool = False
) -> AccountRecord:
    query = select(AccountRecord).where(AccountRecord.id == account_id)
    account = await session.scalar(query.with_for_update() if lock else query)
    if account is None or account.deleted_at is not None:
        raise failure("account_not_found", "Account not found.", ErrorCategory.not_found)
    await authorize_workspace(
        session, actor=actor, workspace_id=account.workspace_id, action=WorkspaceAction.bot_memory_share
    )
    return account


async def _project(session: AsyncSession, policy: SharingPolicyRecord) -> SharingPolicy:
    participants = tuple(
        await session.scalars(
            select(SharingParticipantRecord)
            .where(SharingParticipantRecord.policy_id == policy.id)
            .order_by(SharingParticipantRecord.scope_id)
        )
    )
    return SharingPolicy.model_validate(
        {
            "id": policy.id,
            "name": policy.name,
            "scope_ids": tuple(row.scope_id for row in participants),
            "participants": tuple(
                {"scope_id": row.scope_id, "joined_at": assume_utc(row.joined_at)} for row in participants
            ),
            "kinds": policy.kinds_json,
            "include_history": policy.include_history,
            "enroll_future_groups": policy.enroll_future_groups,
            "enabled": policy.enabled,
            "version": policy.version,
            "created_at": assume_utc(policy.created_at),
            "future_since": assume_utc(policy.future_since),
        }
    )


async def save_policy(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    body: SharingPolicyInput,
    policy_id: str | None = None,
) -> SharingPolicy:
    async with transaction(service.sessions) as session:
        account = await _account(session, actor, account_id, lock=True)
        settings = (await read_settings(session, account.id)).memory
        if settings is None:
            raise failure("bot_memory_disabled", "Select a Memory Provider first.", ErrorCategory.conflict)
        provider_id = settings.provider_id
        if len(set(body.scope_ids)) != len(body.scope_ids):
            raise failure("duplicate_group", "Select each group once.", ErrorCategory.invalid_request)
        if not body.kinds or len(set(body.kinds)) != len(body.kinds):
            raise failure("invalid_memory_kinds", "Select distinct memory kinds.", ErrorCategory.invalid_request)
        now = utc_now()
        if policy_id is None:
            await eligible_groups(session, account_id, provider_id, body.scope_ids)
            policy = SharingPolicyRecord(
                id=new_object_id("mshare"),
                account_id=account_id,
                provider_id=provider_id,
                name=body.name,
                kinds_json=list(body.kinds),
                include_history=body.include_history,
                enroll_future_groups=body.enroll_future_groups,
                enabled=body.enabled,
                version=1,
                created_at=now,
                future_since=now,
            )
            session.add(policy)
            await session.flush()
            previous: dict[str, SharingParticipantRecord] = {}
        else:
            policy = await session.scalar(
                select(SharingPolicyRecord)
                .where(
                    SharingPolicyRecord.id == policy_id,
                    SharingPolicyRecord.account_id == account_id,
                    SharingPolicyRecord.provider_id == provider_id,
                )
                .with_for_update()
            )
            if policy is None:
                raise failure("sharing_policy_not_found", "Sharing policy not found.", ErrorCategory.not_found)
            if not isinstance(body, ReplaceSharingPolicy):
                raise failure("version_required", "A version is required.", ErrorCategory.invalid_request)
            require_version(policy.version, body.expected_version)
            reset = (not policy.enabled and body.enabled) or (policy.include_history and not body.include_history)
            if reset:
                policy.future_since = now
            previous = {
                row.scope_id: row
                for row in await session.scalars(
                    select(SharingParticipantRecord).where(SharingParticipantRecord.policy_id == policy.id)
                )
            }
            # Removing grants and disabling must work even after a group becomes unavailable.
            # Retained participants remain fenced by live eligibility on every read.
            added = tuple(scope_id for scope_id in body.scope_ids if scope_id not in previous)
            await eligible_groups(
                session,
                account_id,
                provider_id,
                body.scope_ids if not policy.enabled and body.enabled else added,
            )
            if reset:
                for row in previous.values():
                    row.joined_at = now
            policy.name, policy.kinds_json = body.name, list(body.kinds)
            policy.include_history, policy.enroll_future_groups, policy.enabled = (
                body.include_history,
                body.enroll_future_groups,
                body.enabled,
            )
            policy.version += 1
        for scope_id, row in previous.items():
            if scope_id not in body.scope_ids:
                await session.delete(row)
        for scope_id in body.scope_ids:
            if scope_id not in previous:
                session.add(SharingParticipantRecord(policy_id=policy.id, scope_id=scope_id, joined_at=now))
        await session.flush()
        await audit(
            session,
            actor,
            organization_id=account.organization_id,
            workspace_id=account.workspace_id,
            action="sharing_policy_save",
            resource_id=policy.id,
            details={
                "version": policy.version,
                "enabled": policy.enabled,
                "participant_count": len(body.scope_ids),
                "include_history": policy.include_history,
                "enroll_future_groups": policy.enroll_future_groups,
            },
        )
        return await _project(session, policy)


async def list_policies(
    service: BotMemoryService, actor: AuthenticatedActor, account_id: str, *, limit: int = 50, cursor: str | None = None
) -> SharingPolicyCollection:
    async with short_session(service.sessions) as session:
        account = await _account(session, actor, account_id)
        settings = (await read_settings(session, account.id)).memory
        if settings is None:
            return SharingPolicyCollection(items=())
        provider_id = settings.provider_id
        binding = {
            "account_id": account_id,
            "provider_id": provider_id,
            "limit": limit,
            "collection": "sharing-policies",
        }
        query = select(SharingPolicyRecord).where(
            SharingPolicyRecord.account_id == account_id, SharingPolicyRecord.provider_id == provider_id
        )
        if cursor:
            query = query.where(SharingPolicyRecord.id > service._cursor(cursor, binding))
        rows = list(await session.scalars(query.order_by(SharingPolicyRecord.id).limit(limit + 1)))
        from a13n_service.collection_cursors import encode_collection_cursor

        continuation = (
            encode_collection_cursor({"id": rows[limit - 1].id}, scope=binding, kind="bot-memory")
            if len(rows) > limit
            else None
        )
        return SharingPolicyCollection(
            items=tuple([await _project(session, row) for row in rows[:limit]]), next_cursor=continuation
        )
