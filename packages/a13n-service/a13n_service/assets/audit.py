"""Bounded Asset security evidence and denied-operation audit transactions."""

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.models import SecurityAuditRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now


def asset_audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str,
    asset_id: str | None,
    action: str,
    source_kind: str | None,
    now: datetime,
    outcome: Literal["success", "failure"] = "success",
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type="asset",
        resource_id=asset_id,
        outcome=outcome,
        occurred_at=now,
        details={} if source_kind is None else {"source_kind": source_kind},
    )


async def record_denied(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    asset_id: str | None,
    action: str,
    source_kind: str | None = "upload",
    clock: Clock = utc_now,
) -> None:
    try:
        async with transaction(sessions) as session:
            organization_id = await session.scalar(
                select(WorkspaceRecord.organization_id).where(WorkspaceRecord.id == workspace_id)
            )
            session.add(
                asset_audit_record(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    asset_id=asset_id,
                    action=action,
                    source_kind=source_kind,
                    now=clock(),
                    outcome="failure",
                )
            )
    except Exception:
        return
