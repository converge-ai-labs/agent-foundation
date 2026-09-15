"""Resume Web Provider credential erasure for terminally deleted Workspaces."""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import WebProviderRecord


class WebProviderOwnerCleanup:
    """Remaining active rows are the durable progress authority; disablement is reversible."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, batch_limit: int) -> None:
        self._sessions = sessions
        self._batch_limit = batch_limit

    async def scan(self) -> Sweep:
        now = utc_now()
        deleted_workspace = exists().where(
            WorkspaceRecord.id == WebProviderRecord.workspace_id,
            WorkspaceRecord.organization_id == WebProviderRecord.organization_id,
            WorkspaceRecord.deleted_at.is_not(None),
        )
        async with transaction(self._sessions) as database:
            records = tuple(
                await database.scalars(
                    select(WebProviderRecord)
                    .where(WebProviderRecord.ciphertext.is_not(None), deleted_workspace)
                    .order_by(WebProviderRecord.created_at, WebProviderRecord.id)
                    .limit(self._batch_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                record.clear_credential()
                record.enabled = False
                record.updated_at = now
                database.add(
                    security_audit_record(
                        audit_id=new_object_id("aud"),
                        actor=SystemAuditActor(request_id=None),
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        action="web_provider.owner_cleanup",
                        resource_type="web_provider",
                        resource_id=record.id,
                        outcome="success",
                        occurred_at=now,
                        details={"reason": "owner_deleted"},
                    )
                )
            age = max((now - assume_utc(row.created_at)).total_seconds() for row in records) if records else None
        return Sweep(examined=len(records), completed=len(records), oldest_age_seconds=age)
