"""Queue transitions shared with atomic Run acceptance."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.domain import PrincipalRef

from .control_domain import QueuedSubmissionFailure
from .control_models import QueuedSubmissionRecord


class QueueConsumptionConflict(RuntimeError):
    """The selected queue head no longer matches prepared Run acceptance."""


async def consume_first_submission(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
    queued_submission_id: str,
    submission_digest_sha256: str,
    authority_principal: PrincipalRef,
    consumed_run_id: str,
    consumption_key: str | None = None,
    now: datetime,
) -> QueuedSubmissionRecord:
    """Select, validate, and consume exactly the current first queued row."""

    selected, remaining = await _lock_selected_head(
        database,
        organization_id=organization_id,
        thread_id=thread_id,
        queued_submission_id=queued_submission_id,
        submission_digest_sha256=submission_digest_sha256,
    )
    if (
        selected.authority_principal_type != authority_principal.principal_type.value
        or selected.authority_principal_id != authority_principal.principal_id
    ):
        raise QueueConsumptionConflict("prepared queued submission authority changed")

    selected.position = None
    selected.consumed_run_id = consumed_run_id
    selected.consumption_key = consumption_key
    selected.consumed_at = now
    selected.updated_at = now
    selected.version += 1
    await _compact_after_head(database, remaining)
    return selected


async def fail_first_submission(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
    queued_submission_id: str,
    submission_digest_sha256: str,
    failure: QueuedSubmissionFailure,
    now: datetime,
) -> QueuedSubmissionRecord:
    """Terminally fail exactly the current queue head and compact live order."""

    selected, remaining = await _lock_selected_head(
        database,
        organization_id=organization_id,
        thread_id=thread_id,
        queued_submission_id=queued_submission_id,
        submission_digest_sha256=submission_digest_sha256,
    )
    selected.position = None
    selected.failure_json = failure.model_dump(mode="json")
    selected.failed_at = now
    selected.updated_at = now
    selected.version += 1
    await _compact_after_head(database, remaining)
    return selected


async def _lock_selected_head(
    database: AsyncSession,
    *,
    organization_id: str,
    thread_id: str,
    queued_submission_id: str,
    submission_digest_sha256: str,
) -> tuple[QueuedSubmissionRecord, tuple[QueuedSubmissionRecord, ...]]:
    rows = tuple(
        (
            await database.scalars(
                select(QueuedSubmissionRecord)
                .where(
                    QueuedSubmissionRecord.organization_id == organization_id,
                    QueuedSubmissionRecord.thread_id == thread_id,
                    QueuedSubmissionRecord.position.is_not(None),
                )
                .order_by(QueuedSubmissionRecord.position, QueuedSubmissionRecord.id)
                .with_for_update()
            )
        ).all()
    )
    if not rows or rows[0].id != queued_submission_id:
        raise QueueConsumptionConflict("prepared queued submission is no longer first")
    selected = rows[0]
    if selected.submission_digest_sha256 != submission_digest_sha256:
        raise QueueConsumptionConflict("prepared queued submission intent changed")
    return selected, rows[1:]


async def _compact_after_head(
    database: AsyncSession,
    remaining: tuple[QueuedSubmissionRecord, ...],
) -> None:
    if not remaining:
        return
    offset = len(remaining) + 1
    for row in remaining:
        if row.position is None:
            raise QueueConsumptionConflict("live queued submission position is missing")
        row.position += offset
    await database.flush()
    for row in remaining:
        assert row.position is not None
        row.position -= offset + 1


__all__ = [
    "QueueConsumptionConflict",
    "consume_first_submission",
    "fail_first_submission",
]
