"""Steer replay evidence owned by the durable inbox entry."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
)
from a13n_service.iam import PrincipalRef

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
