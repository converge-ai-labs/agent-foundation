"""Enroll a group's first eligible configuration, never a later reconnection."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.memory.service import failure
from a13n_service.temporal import utc_now

from .audit import audit
from .domain import ScopeSettings
from .models import ScopeRecord, SharingParticipantRecord, SharingPolicyRecord


async def initialize_sharing(session: AsyncSession, actor: AuthenticatedActor, scope: ScopeRecord) -> None:
    # Caller holds the Account lock, serializing policy edits and group configuration.
    if (
        scope.sharing_initialized
        or scope.audience not in ("public", "private")
        or not ScopeSettings.model_validate(scope.settings_json).enabled
    ):
        return
    scope.sharing_initialized = True
    policies = await session.scalars(
        select(SharingPolicyRecord).where(
            SharingPolicyRecord.account_id == scope.account_id,
            SharingPolicyRecord.provider_id == scope.provider_id,
            SharingPolicyRecord.enabled.is_(True),
            SharingPolicyRecord.enroll_future_groups.is_(True),
        )
    )
    now = utc_now()
    for policy in policies:
        if await session.get(SharingParticipantRecord, (policy.id, scope.id)) is not None:
            continue
        count = await session.scalar(
            select(func.count())
            .select_from(SharingParticipantRecord)
            .where(SharingParticipantRecord.policy_id == policy.id)
        )
        if count is not None and count >= 128:
            raise failure(
                "sharing_participant_limit",
                "A sharing policy has reached 128 groups. Adjust its participants or future enrollment first.",
                ErrorCategory.conflict,
            )
        session.add(SharingParticipantRecord(policy_id=policy.id, scope_id=scope.id, joined_at=now))
        policy.version += 1
        await audit(
            session,
            actor,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            action="sharing_policy_enroll",
            resource_id=policy.id,
            details={"version": policy.version, "scope_id": scope.id},
        )
