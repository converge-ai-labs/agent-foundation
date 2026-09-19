"""Shared fenced state transitions for durable Outbox intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Update, and_, case, or_, select, true, update
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
    candidates = select(OutboxRecord.id).where(
        OutboxRecord.source_kind == source_kind,
        OutboxRecord.destination_kind == destination_kind,
        due,
    )
    if destination_ref is not None:
        candidates = candidates.where(OutboxRecord.destination_ref == destination_ref)
    selected = (
        candidates.order_by(OutboxRecord.available_at, OutboxRecord.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("claimable_outbox")
    )
    await database.flush()
    records = (
        await database.scalars(
            update(OutboxRecord)
            .where(OutboxRecord.id == selected.c.id)
            .values(
                status="publishing",
                claim_generation=OutboxRecord.claim_generation + 1,
                attempt_count=OutboxRecord.attempt_count + 1,
                lease_expires_at=now + lease_duration,
                updated_at=now,
                published_at=None,
                dead_lettered_at=None,
            )
            .returning(OutboxRecord)
            .execution_options(synchronize_session="fetch", populate_existing=True)
        )
    ).all()
    # UPDATE RETURNING has no ordering guarantee; retain the admission order.
    return tuple(
        OutboxClaim(
            outbox_id=record.id,
            source_kind=record.source_kind,
            source_id=record.source_id,
            destination_kind=record.destination_kind,
            destination_ref=record.destination_ref,
            generation=record.claim_generation,
            attempt_count=record.attempt_count,
        )
        for record in sorted(records, key=lambda record: (record.available_at, record.id))
    )


async def complete_outbox(
    database: AsyncSession,
    claim: OutboxClaim,
    *,
    completed_at: datetime,
) -> bool:
    await database.flush()
    return (
        await database.scalar(
            _current_claim_update(claim, settled_at=completed_at).values(
                status="published",
                lease_expires_at=None,
                updated_at=completed_at,
                published_at=completed_at,
                dead_lettered_at=None,
                last_error_code=None,
            )
        )
        is not None
    )


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
    dead_lettered = true() if not retryable else OutboxRecord.attempt_count >= max_attempts
    await database.flush()
    return (
        await database.scalar(
            _current_claim_update(claim, settled_at=failed_at).values(
                status=case((dead_lettered, "dead_lettered"), else_="pending"),
                available_at=case((dead_lettered, failed_at), else_=failed_at + retry_after),
                lease_expires_at=None,
                updated_at=failed_at,
                published_at=None,
                dead_lettered_at=case((dead_lettered, failed_at), else_=None),
                last_error_code=error_code,
            )
        )
        is not None
    )


async def redrive_outbox(
    database: AsyncSession,
    *,
    outbox_id: str,
    source_kind: str,
    destination_kind: str,
    destination_ref: str,
    redriven_at: datetime,
) -> bool:
    await database.flush()
    return (
        await database.scalar(
            update(OutboxRecord)
            .where(
                OutboxRecord.id == outbox_id,
                OutboxRecord.source_kind == source_kind,
                OutboxRecord.destination_kind == destination_kind,
                OutboxRecord.destination_ref == destination_ref,
                OutboxRecord.status == "dead_lettered",
            )
            .values(
                status="pending",
                available_at=redriven_at,
                attempt_count=0,
                lease_expires_at=None,
                updated_at=redriven_at,
                published_at=None,
                dead_lettered_at=None,
                last_error_code=None,
            )
            .returning(OutboxRecord.id)
            .execution_options(synchronize_session="fetch")
        )
        is not None
    )


def _current_claim_update(claim: OutboxClaim, *, settled_at: datetime) -> Update:
    """Keep settlement and its complete claim fence in one statement."""

    return (
        update(OutboxRecord)
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
        .returning(OutboxRecord.id)
        .execution_options(synchronize_session="fetch")
    )


__all__ = ["OutboxClaim", "claim_outbox", "complete_outbox", "fail_outbox", "redrive_outbox"]
