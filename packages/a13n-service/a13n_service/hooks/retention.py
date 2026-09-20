"""Bounded Hook collection with inheritance, delivery, replay, and audit pins."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import String, cast, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord, OutboxRecord
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import HookSubscriptionRecord as Head
from .models import HookSubscriptionRevisionRecord as Revision


class HookRetention:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, minimum_age: timedelta, batch_limit: int) -> None:
        self._sessions = sessions
        self._minimum_age = minimum_age
        self._batch_limit = batch_limit
        self._after_id = ""

    async def scan(self) -> Sweep:
        now = utc_now()
        cutoff = now - self._minimum_age
        completed_source = exists().where(
            RunRecord.organization_id == Head.organization_id,
            RunRecord.id == Head.inline_run_id,
            RunRecord.status == "completed",
        )
        completed = deferred = 0
        async with transaction(self._sessions) as database:
            candidates = tuple(
                (
                    await database.execute(
                        select(Revision, Head)
                        .join(Head, Head.id == Revision.hook_subscription_id)
                        .where(
                            Revision.id > self._after_id,
                            Revision.created_at < cutoff,
                            Head.updated_at < cutoff,
                            or_(Head.inline_run_id.is_(None), completed_source),
                        )
                        .order_by(Revision.id)
                        .limit(self._batch_limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for revision, head in candidates:
                audit = exists().where(
                    SecurityAuditRecord.organization_id == head.organization_id,
                    or_(
                        SecurityAuditRecord.resource_id.in_((head.id, revision.id)),
                        cast(SecurityAuditRecord.details, String).contains(head.id),
                        cast(SecurityAuditRecord.details, String).contains(revision.id),
                    ),
                )
                evidence = exists().where(
                    IdempotencyEvidenceRecord.organization_id == head.organization_id,
                    IdempotencyEvidenceRecord.expires_at > now,
                    or_(
                        IdempotencyEvidenceRecord.scope_id.in_((head.id, revision.id)),
                        IdempotencyEvidenceRecord.result_ref.in_((head.id, revision.id)),
                        IdempotencyEvidenceRecord.result_ref == head.inline_run_id,
                        cast(IdempotencyEvidenceRecord.receipt_json, String).contains(head.id),
                        cast(IdempotencyEvidenceRecord.receipt_json, String).contains(revision.id),
                    ),
                )
                dispatch = exists().where(
                    LifecycleEventRecord.organization_id == head.organization_id,
                    LifecycleEventRecord.run_id == head.inline_run_id,
                    LifecycleEventRecord.hook_dispatch_state != "done",
                )
                delivery = exists().where(OutboxRecord.destination_ref == revision.id)
                if await database.scalar(select(or_(audit, evidence, dispatch, delivery))):
                    deferred += 1
                    continue
                if revision.id == head.current_revision_id:
                    other_revision = exists().where(
                        Revision.hook_subscription_id == head.id, Revision.id != revision.id
                    )
                    if (head.deleted_at is None and head.expired_at is None) or await database.scalar(
                        select(other_revision)
                    ):
                        deferred += 1
                        continue
                    # The deferred cycle permits atomic collection of both rows.
                    removed_head = await database.scalar(delete(Head).where(Head.id == head.id).returning(Head.id))
                    if removed_head is None:
                        continue
                    completed += 1
                removed_revision = await database.scalar(
                    delete(Revision).where(Revision.id == revision.id).returning(Revision.id)
                )
                completed += int(removed_revision is not None)
            self._after_id = candidates[-1][0].id if candidates else ""
        return Sweep(
            examined=len(candidates),
            completed=completed,
            deferred=deferred,
            oldest_age_seconds=max(
                ((now - assume_utc(revision.created_at)).total_seconds() for revision, _ in candidates), default=None
            ),
        )
