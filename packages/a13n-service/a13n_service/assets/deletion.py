"""Atomic Asset tombstoning shared by explicit deletion and owner cleanup."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import AuthenticatedActor
from a13n_service.ids import new_object_id

from .models import AssetRecord
from .objects import ASSET_OBJECT_DESTINATION


def tombstone_asset(
    session: AsyncSession, record: AssetRecord, *, actor: AuthenticatedActor | SystemAuditActor, now: datetime
) -> None:
    """The caller locks and validates the active Asset before this transition."""
    record.deleted_at = now
    session.add(
        security_audit_record(
            audit_id=new_object_id("aud"),
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            action="asset.delete",
            resource_type="asset",
            resource_id=record.id,
            outcome="success",
            occurred_at=now,
            details={"source_kind": record.source_kind},
        )
    )
    session.add(
        OutboxRecord(
            id=new_object_id("obx"),
            source_kind="asset",
            source_id=record.id,
            destination_kind="asset_content_cleanup",
            destination_ref=ASSET_OBJECT_DESTINATION,
            status="pending",
            available_at=now,
            claim_generation=0,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
    )
