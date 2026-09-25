"""The backlog sweep: how much due work waits to be claimed, and for how long, reported as gauges.

Due work is an accepted run whose `available_at` has passed, or an unleased pending outbox row whose
`available_at` has passed. Waiting runs and work scheduled for later are not backlog. Every control replica
reports the same values, so dashboards take their maximum; a scrape never queries the database.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from a13n_service.infra.db import Storage, now, short_session
from a13n_service.infra.outbox import OutboxKind, OutboxRow
from a13n_service.infra.telemetry import Gauge
from a13n_service.runs.tables import RunRow

# A count past this bound is reported as the bound, so one pass stays cheap however large the backlog grows.
COUNT_LIMIT = 10_000
REPORT_SECONDS = 15

BACKLOG_SIZE = Gauge(
    "a13n.backlog.size", unit="{item}", description=f"Due work waiting to be claimed, by queue, up to {COUNT_LIMIT}"
)
BACKLOG_OLDEST_AGE = Gauge(
    "a13n.backlog.oldest_age", unit="s", description="How long the oldest due item of each queue has waited"
)


async def report_backlog(storage: Storage) -> None:
    async with short_session(storage) as session:
        current = await now(session)
        queues = {"runs": await _due(session, RunRow.available_at, current, RunRow.status == "accepted")}
        for kind in get_args(OutboxKind.__value__):
            queues[kind] = await _due(
                session,
                OutboxRow.available_at,
                current,
                OutboxRow.kind == kind,
                OutboxRow.status == "pending",
                or_(OutboxRow.lease_expires_at.is_(None), OutboxRow.lease_expires_at <= current),
            )
    for queue, (size, oldest) in queues.items():
        BACKLOG_SIZE.set(size, {"queue": queue})
        BACKLOG_OLDEST_AGE.set((current - oldest).total_seconds() if oldest is not None else 0, {"queue": queue})


async def _due(
    session: AsyncSession, available_at: InstrumentedAttribute[datetime], current: datetime, *where: ColumnElement[bool]
) -> tuple[int, datetime | None]:
    """The number of due rows, up to `COUNT_LIMIT`, and the earliest `available_at` among them."""
    due = select(available_at.label("at")).where(*where, available_at <= current).order_by(available_at)
    rows = due.limit(COUNT_LIMIT).subquery()
    size, oldest = (await session.execute(select(func.count(), func.min(rows.c.at)))).one()
    return size, oldest
