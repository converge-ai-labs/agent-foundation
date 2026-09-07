"""Shared fenced state transitions for durable Outbox intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import OutboxRecord


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    outbox_id: str
    source_kind: str
    source_id: str
    destination_kind: str
    destination_ref: str
    generation: int
    attempt_count: int


async def claim_outbox(
    database: AsyncSession,
    *,
    source_kind: str,
    destination_kind: str,
    destination_ref: str | None = None,
    now: datetime,
    lease_duration: timedelta,
    limit: int,
) -> tuple[OutboxClaim, ...]:
    if not source_kind or not destination_kind or lease_duration <= timedelta(0):
        raise ValueError("Outbox claim selector and lease are required")
    if limit < 1 or limit > 200:
        raise ValueError("Outbox claim limit must be between 1 and 200")
    due = or_(
        and_(OutboxRecord.status == "pending", OutboxRecord.available_at <= now),
        and_(OutboxRecord.status == "publishing", OutboxRecord.lease_expires_at <= now),
    )
    statement = select(OutboxRecord).where(
        OutboxRecord.source_kind == source_kind,
        OutboxRecord.destination_kind == destination_kind,
        due,
    )
    if destination_ref is not None:
        statement = statement.where(OutboxRecord.destination_ref == destination_ref)
    records = (
        await database.scalars(
            statement.order_by(OutboxRecord.available_at, OutboxRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claims: list[OutboxClaim] = []
    for record in records:
        record.status = "publishing"
        record.claim_generation += 1
        record.attempt_count += 1
        record.lease_expires_at = now + lease_duration
        record.updated_at = now
        record.published_at = None
        record.dead_lettered_at = None
        claims.append(
            OutboxClaim(
                outbox_id=record.id,
                source_kind=record.source_kind,
                source_id=record.source_id,
                destination_kind=record.destination_kind,
                destination_ref=record.destination_ref,
                generation=record.claim_generation,
                attempt_count=record.attempt_count,
            )
        )
    await database.flush()
    return tuple(claims)


async def complete_outbox(
    database: AsyncSession,
    claim: OutboxClaim,
    *,
    completed_at: datetime,
) -> bool:
    record = await _lock_current_claim(database, claim, settled_at=completed_at)
    if record is None:
        return False
    record.status = "published"
    record.lease_expires_at = None
    record.updated_at = completed_at
    record.published_at = completed_at
    record.dead_lettered_at = None
    record.last_error_code = None
    await database.flush()
    return True


async def fail_outbox(
    database: AsyncSession,
    claim: OutboxClaim,
    *,
    failed_at: datetime,
    error_code: str,
    retryable: bool,
    retry_after: timedelta,
    max_attempts: int,
) -> bool:
    if not error_code or len(error_code) > 64:
        raise ValueError("Outbox error code must contain 1 through 64 characters")
    if retry_after < timedelta(0) or max_attempts < 1:
        raise ValueError("Outbox retry policy is invalid")
    record = await _lock_current_claim(database, claim, settled_at=failed_at)
    if record is None:
        return False
    dead_lettered = not retryable or record.attempt_count >= max_attempts
    record.status = "dead_lettered" if dead_lettered else "pending"
    record.available_at = failed_at if dead_lettered else failed_at + retry_after
    record.lease_expires_at = None
    record.updated_at = failed_at
    record.published_at = None
    record.dead_lettered_at = failed_at if dead_lettered else None
    record.last_error_code = error_code
    await database.flush()
    return True


async def redrive_outbox(
    database: AsyncSession,
    *,
    outbox_id: str,
    source_kind: str,
    destination_kind: str,
    destination_ref: str,
    redriven_at: datetime,
) -> bool:
    record = await database.scalar(
        select(OutboxRecord)
        .where(
            OutboxRecord.id == outbox_id,
            OutboxRecord.source_kind == source_kind,
            OutboxRecord.destination_kind == destination_kind,
            OutboxRecord.destination_ref == destination_ref,
            OutboxRecord.status == "dead_lettered",
        )
        .with_for_update()
    )
    if record is None:
        return False
    record.status = "pending"
    record.available_at = redriven_at
    record.attempt_count = 0
    record.lease_expires_at = None
    record.updated_at = redriven_at
    record.published_at = None
    record.dead_lettered_at = None
    record.last_error_code = None
    await database.flush()
    return True


async def _lock_current_claim(
    database: AsyncSession,
    claim: OutboxClaim,
    *,
    settled_at: datetime,
) -> OutboxRecord | None:
    return await database.scalar(
        select(OutboxRecord)
        .where(
            OutboxRecord.id == claim.outbox_id,
            OutboxRecord.source_kind == claim.source_kind,
            OutboxRecord.source_id == claim.source_id,
            OutboxRecord.destination_kind == claim.destination_kind,
            OutboxRecord.destination_ref == claim.destination_ref,
            OutboxRecord.status == "publishing",
            OutboxRecord.claim_generation == claim.generation,
            OutboxRecord.lease_expires_at > settled_at,
        )
        .with_for_update()
    )


__all__ = ["OutboxClaim", "claim_outbox", "complete_outbox", "fail_outbox", "redrive_outbox"]
