"""Steer replay evidence owned by the durable inbox entry."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.idempotency import (
    IDEMPOTENCY_EVIDENCE_TTL,
    IdempotencyConflict,
    IdempotencyIdentity,
)
from a13n_service.iam import PrincipalRef
from a13n_service.temporal import assume_utc

from .control_domain import SteerReceipt
from .control_models import ThreadInboxRecord


@dataclass(frozen=True, slots=True)
class SteerIdempotency:
    principal: PrincipalRef
    identity: IdempotencyIdentity

    def bind(self, row: ThreadInboxRecord, now: datetime) -> None:
        row.idempotency_actor_type = self.principal.principal_type.value
        row.idempotency_actor_id = self.principal.principal_id
        row.idempotency_key_digest = self.identity.key_digest
        row.idempotency_request_digest = self.identity.request_digest
        row.idempotency_expires_at = now + IDEMPOTENCY_EVIDENCE_TTL


async def find_steer(
    database: AsyncSession,
    *,
    organization_id: str,
    run_id: str,
    idempotency: SteerIdempotency,
    now: datetime,
    locked: bool = False,
) -> ThreadInboxRecord | None:
    """Read replay evidence; replacement requires the caller's owning Thread lock."""
    query = select(ThreadInboxRecord).where(
        ThreadInboxRecord.organization_id == organization_id,
        ThreadInboxRecord.kind == "steer",
        ThreadInboxRecord.accepted_against_run_id == run_id,
        ThreadInboxRecord.idempotency_actor_type == idempotency.principal.principal_type.value,
        ThreadInboxRecord.idempotency_actor_id == idempotency.principal.principal_id,
        ThreadInboxRecord.idempotency_key_digest == idempotency.identity.key_digest,
    )
    if locked:
        query = query.with_for_update()
    row = await database.scalar(query)
    if row is None:
        return None
    assert row.idempotency_expires_at is not None
    if assume_utc(row.idempotency_expires_at) <= assume_utc(now):
        if locked:
            clear_steer_idempotency(row)
            # Release the unique key before inserting its replacement in this transaction.
            await database.flush()
        return None
    if row.idempotency_request_digest != idempotency.identity.request_digest:
        raise IdempotencyConflict
    return row


def steer_receipt(row: ThreadInboxRecord, *, session_id: str) -> SteerReceipt:
    assert row.accepted_against_run_id is not None
    return SteerReceipt(
        session_id=session_id,
        thread_id=row.thread_id,
        run_id=row.accepted_against_run_id,
        steer_id=row.id,
        delivery_sequence=row.delivery_sequence,
        accepted_at=row.created_at,
    )


def clear_steer_idempotency(row: ThreadInboxRecord) -> None:
    row.idempotency_actor_type = None
    row.idempotency_actor_id = None
    row.idempotency_key_digest = None
    row.idempotency_request_digest = None
    row.idempotency_expires_at = None


async def clear_expired_steer_idempotency(database: AsyncSession, *, now: datetime, limit: int) -> int:
    """Clear bounded expired metadata without deleting or retargeting accepted input."""
    rows = tuple(
        (
            await database.scalars(
                select(ThreadInboxRecord)
                .where(ThreadInboxRecord.idempotency_expires_at <= now)
                .order_by(ThreadInboxRecord.idempotency_expires_at, ThreadInboxRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        clear_steer_idempotency(row)
    await database.flush()
    return len(rows)
