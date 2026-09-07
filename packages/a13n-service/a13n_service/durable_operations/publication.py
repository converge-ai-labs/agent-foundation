"""Bounded dispatch and safe observations for a claimed Outbox batch."""

import logging
from collections.abc import Awaitable, Callable

import anyio
import httpx2
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.storage import ObjectStoreError, short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .models import OutboxRecord
from .outbox import OutboxClaim

logger = logging.getLogger("a13n_service.durable_operations.publication")


async def dispatch_outbox_batch(
    sessions: async_sessionmaker[AsyncSession],
    claims: tuple[OutboxClaim, ...],
    publish: Callable[[OutboxClaim], Awaitable[None]],
    *,
    timeout_seconds: float,
    concurrency: int,
    clock: Clock = utc_now,
) -> Sweep:
    if not claims:
        return Sweep()
    limiter = anyio.CapacityLimiter(concurrency)
    failures: set[str] = set()

    async def dispatch(claim: OutboxClaim) -> None:
        async with limiter:
            try:
                with anyio.fail_after(timeout_seconds):
                    await publish(claim)
            except (DBAPIError, ObjectStoreError, httpx2.HTTPError, OSError, TimeoutError) as error:
                failures.add(claim.outbox_id)
                logger.warning(
                    "outbox_dispatch_retry",
                    extra={
                        "event": "outbox_dispatch_retry",
                        "delivery_id": claim.outbox_id,
                        "failure_type": type(error).__name__,
                    },
                )

    async with anyio.create_task_group() as tasks:
        for claim in claims:
            tasks.start_soon(dispatch, claim)
    generations = {claim.outbox_id: claim.generation for claim in claims}
    async with short_session(sessions) as database:
        records = tuple(await database.scalars(select(OutboxRecord).where(OutboxRecord.id.in_(generations))))
    completed = 0
    for record in records:
        if record.claim_generation != generations[record.id]:
            continue
        if record.status == "published":
            completed += 1
            failures.discard(record.id)
        elif record.status == "dead_lettered":
            failures.add(record.id)
    return Sweep(
        examined=len(claims),
        completed=completed,
        failed=len(failures),
        deferred=len(claims) - completed - len(failures),
        oldest_age_seconds=max(
            ((clock() - assume_utc(row.created_at)).total_seconds() for row in records), default=None
        ),
    )
