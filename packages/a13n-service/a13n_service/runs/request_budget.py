"""Run-wide dispatch occupancy, independent of Attempt and usage delivery lifetimes."""

from pydantic_ai.exceptions import UsageLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.tables import ModelDispatchRow, RunRow, UsageRecordRow


async def reserve(
    session: AsyncSession,
    *,
    run_id: str,
    attempt_id: str,
    call_id: str,
    limit: int | None,
    continuation: bool = False,
) -> None:
    """Admit under the Run lock, in the same transaction as the paid-call policy.

    A dispatch without a report remains occupied after owner loss. A report replaces
    its occupancy by call ID; it never adds a second debit. Continuation polls consume
    no new slot, but a provider returning a new generation is counted by its usage fact.
    """
    run = await session.scalar(select(RunRow.id).where(RunRow.id == run_id).with_for_update())
    if run is None:
        raise ServiceError("not_found", "Model dispatch names no Run")
    existing = await session.get(ModelDispatchRow, call_id)
    if existing is not None:
        if existing.run_id != run_id or existing.run_attempt_id != attempt_id:
            raise ServiceError("conflict", "Model dispatch changed its owner")
        return
    if limit is not None:
        occupied = await occupancy(session, run_id)
        if occupied > limit or (not continuation and occupied == limit):
            raise UsageLimitExceeded(f"The request_limit of {limit} was exceeded")
    if not continuation:
        session.add(ModelDispatchRow(call_id=call_id, run_id=run_id, run_attempt_id=attempt_id))
        await session.flush()


async def reconcile(session: AsyncSession, *, run_id: str, limit: int | None) -> None:
    """Check newly observed generations after their usage facts have committed."""
    if limit is None:
        return
    await session.scalar(select(RunRow.id).where(RunRow.id == run_id).with_for_update())
    if await occupancy(session, run_id) > limit:
        raise UsageLimitExceeded(f"The request_limit of {limit} was exceeded")


async def occupancy(session: AsyncSession, run_id: str) -> int:
    """Reported generations plus dispatches whose first report is still pending."""
    models = select(UsageRecordRow.id).where(
        UsageRecordRow.run_id == run_id, UsageRecordRow.record["kind"].astext == "model"
    )
    reported = (
        select(UsageRecordRow.id)
        .where(
            UsageRecordRow.run_id == run_id,
            UsageRecordRow.call_id == ModelDispatchRow.call_id,
            UsageRecordRow.record["kind"].astext == "model",
        )
        .exists()
    )
    pending = select(func.count()).select_from(ModelDispatchRow).where(ModelDispatchRow.run_id == run_id, ~reported)
    used = int(await session.scalar(select(func.count()).select_from(models.subquery())) or 0)
    occupied = int(await session.scalar(pending) or 0)
    return used + occupied
