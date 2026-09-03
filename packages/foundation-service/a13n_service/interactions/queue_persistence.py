"""Queue transitions shared with atomic Run acceptance."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.domain import PrincipalRef

from .control_models import QueuedSubmissionRecord


class QueueConsumptionConflict(RuntimeError):
    """The selected queue head no longer matches prepared Run acceptance."""


async def consume_first_submission(
    database: AsyncSession,
    *,
    tenant_id: str,
    thread_id: str,
    queued_submission_id: str,
    submission_digest_sha256: str,
    authority_principal: PrincipalRef,
    consumed_run_id: str,
    now: datetime,
) -> QueuedSubmissionRecord:
    """Select, validate, and consume exactly the current first queued row."""

    rows = tuple(
        (
            await database.scalars(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.tenant_id == tenant_id,
                    QueuedSubmissionRecord.thread_id == thread_id,
                    QueuedSubmissionRecord.consumed_run_id.is_(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .with_for_update()
            )
        ).all()
    )
    if not rows or rows[0].id != queued_submission_id:
        raise QueueConsumptionConflict("prepared queued submission is no longer first")
    selected = rows[0]
    if (
        selected.submission_digest_sha256 != submission_digest_sha256
        or selected.authority_principal_type != authority_principal.principal_type.value
        or selected.authority_principal_id != authority_principal.principal_id
    ):
        raise QueueConsumptionConflict("prepared queued submission intent or authority changed")

    selected.position = None
    selected.consumed_run_id = consumed_run_id
    selected.consumed_at = now
    selected.updated_at = now
    selected.version += 1
    remaining = rows[1:]
    if remaining:
        offset = len(rows)
        for row in remaining:
            if row.position is None:
                raise QueueConsumptionConflict("live queued submission position is missing")
            row.position += offset
        await database.flush()
        for row in remaining:
            assert row.position is not None
            row.position -= offset + 1
    return selected


__all__ = ["QueueConsumptionConflict", "consume_first_submission"]
