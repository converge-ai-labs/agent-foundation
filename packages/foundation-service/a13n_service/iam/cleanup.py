"""Bounded recovery of terminal owner deletion, preserving independent history."""

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.assets.deletion import tombstone_asset
from a13n_service.assets.models import AssetRecord
from a13n_service.background import Sweep
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .audit import SystemAuditActor, security_audit_record
from .models import RoleBindingRecord, ServiceAccountRecord, WorkspaceRecord


class OwnerCleanup:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, batch_limit: int) -> None:
        self._sessions = sessions
        self._limit = batch_limit
        self._index = 0

    async def scan(self) -> Sweep:
        # Rotate resource classes even when one class has an unlimited backlog.
        model = (ServiceAccountRecord, RoleBindingRecord, AssetRecord)[self._index]
        self._index = (self._index + 1) % 3
        now = utc_now()
        deleted_workspace = exists().where(
            WorkspaceRecord.id == model.workspace_id,
            WorkspaceRecord.organization_id == model.organization_id,
            WorkspaceRecord.deleted_at.is_not(None),
        )
        if model is RoleBindingRecord:
            predicate = or_(
                deleted_workspace,
                exists().where(
                    ServiceAccountRecord.organization_id == model.organization_id,
                    ServiceAccountRecord.id == model.principal_id,
                    model.principal_type == "service_account",
                    ServiceAccountRecord.deleted_at.is_not(None),
                ),
            )
        else:
            predicate = deleted_workspace & (
                ServiceAccountRecord.deleted_at.is_(None)
                if model is ServiceAccountRecord
                else AssetRecord.deleted_at.is_(None)
            )
        async with transaction(self._sessions) as database:
            records = tuple(
                await database.scalars(
                    select(model)
                    .where(predicate)
                    .order_by(model.created_at, model.id)
                    .limit(self._limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                actor = SystemAuditActor(request_id=None)
                if isinstance(record, AssetRecord):
                    tombstone_asset(database, record, actor=actor, now=now)
                    continue
                resource_type = "service_account" if isinstance(record, ServiceAccountRecord) else "role_binding"
                database.add(
                    security_audit_record(
                        audit_id=new_object_id("aud"),
                        actor=actor,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        action=f"{resource_type}.delete",
                        resource_type=resource_type,
                        resource_id=record.id,
                        outcome="success",
                        occurred_at=now,
                        details={"reason": "owner_deleted"},
                    )
                )
                if isinstance(record, ServiceAccountRecord):
                    record.deleted_at = now
                    record.updated_at = now
                else:
                    await database.delete(record)
            age = max(((now - assume_utc(record.created_at)).total_seconds() for record in records), default=None)
        return Sweep(examined=len(records), completed=len(records), oldest_age_seconds=age)
