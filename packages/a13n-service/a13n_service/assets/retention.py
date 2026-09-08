"""Collect Asset tombstones only after cleanup, reconciliation, and audit pins end."""

from datetime import timedelta

from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord, OutboxRecord
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import AssetRecord


class AssetRetention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, minimum_age: timedelta, batch_limit: int):
        self._sessions = sessions
        self._age = minimum_age
        self._limit = batch_limit
        self._after_id = ""

    async def scan(self) -> Sweep:
        now = utc_now()
        completed = 0
        async with transaction(self._sessions) as session:
            records = tuple(
                await session.scalars(
                    select(AssetRecord)
                    .where(
                        AssetRecord.deleted_at < now - self._age,
                        AssetRecord.id > self._after_id,
                    )
                    .order_by(AssetRecord.id)
                    .limit(self._limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                pending = exists().where(OutboxRecord.source_kind == "asset", OutboxRecord.source_id == record.id)
                replay = exists().where(
                    IdempotencyEvidenceRecord.organization_id == record.organization_id,
                    IdempotencyEvidenceRecord.expires_at > now,
                    or_(
                        IdempotencyEvidenceRecord.result_ref == record.id,
                        cast(IdempotencyEvidenceRecord.receipt_json, String).contains(record.id),
                    ),
                )
                audit = exists().where(
                    SecurityAuditRecord.organization_id == record.organization_id,
                    or_(
                        SecurityAuditRecord.resource_id == record.id,
                        cast(SecurityAuditRecord.details, String).contains(record.id),
                    ),
                )
                attempt = exists().where(
                    RunAttemptRecord.id == record.source_run_attempt_id,
                    RunAttemptRecord.status.in_(("leased", "running")),
                    RunAttemptRecord.lease_expires_at > now,
                )
                if await session.scalar(select(or_(pending, replay, audit, attempt))):
                    continue
                await session.delete(record)
                completed += 1
            self._after_id = records[-1].id if records else ""
            age = max(
                (
                    (now - assume_utc(record.deleted_at)).total_seconds()
                    for record in records
                    if record.deleted_at is not None
                ),
                default=None,
            )
        return Sweep(
            examined=len(records), completed=completed, deferred=len(records) - completed, oldest_age_seconds=age
        )
