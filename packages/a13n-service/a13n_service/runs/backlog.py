"""The backlog sweep: how much due work waits to be claimed, and for how long, reported as gauges.

Due work is an accepted run whose `available_at` has passed, or an unleased pending outbox row whose
`available_at` has passed. Waiting runs and work scheduled for later are not backlog. Every control replica
reports the same values, so dashboards take their maximum; a scrape never queries the database.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import get_args

from a13n_logging import get_logger
from sqlalchemy import ColumnElement, SQLColumnExpression, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, now, short_session
from a13n_service.infra.outbox import OutboxKind, OutboxRow, Policy
from a13n_service.infra.telemetry import Gauge
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow, ToolExecutionRow

# A count at this bound is a lower bound. LIMIT caps returned candidates, not planner scan or sort work.
COUNT_LIMIT = 10_000
REPORT_SECONDS = 15

BACKLOG_SIZE = Gauge(
    "a13n.backlog.size", unit="{item}", description=f"Due work waiting to be claimed, by queue, up to {COUNT_LIMIT}"
)
BACKLOG_UPDATED = Gauge(
    "a13n.backlog.updated", unit="s", description="Unix time of last successful backlog snapshot by queue"
)
BACKLOG_LIMIT = Gauge(
    "a13n.backlog.limit", unit="{item}", description="Bounded count ceiling; a size at this limit is a lower bound"
)
BACKLOG_OLDEST_AGE = Gauge(
    "a13n.backlog.oldest_age", unit="s", description="How long the oldest due item of each queue has waited"
)


async def report_backlog(
    storage: Storage, policies: Mapping[OutboxKind, Policy] | None = None
) -> dict[str, tuple[int, float]]:
    async with short_session(storage) as session:
        current = await now(session)
        queues = {"runs": await _due(session, RunRow.available_at, current, RunRow.status == "accepted")}
        # Only idle, unpaused threads are actionable by advancement. Deliberate waits and active runs
        # are not stuck work. Inbox wait histograms separately include all business blocking.
        actionable = (
            select(ThreadRow.id)
            .where(
                ThreadRow.id == InboxEntryRow.thread_id,
                ThreadRow.current_run_id.is_(None),
                ThreadRow.archived_at.is_(None),
                ~select(RunRow.id)
                .where(RunRow.id == ThreadRow.last_run_id, RunRow.status.in_(("waiting", "failed", "cancelled")))
                .exists(),
            )
            .exists()
        )
        queues["inbox"] = await _due(
            session, InboxEntryRow.created_at, current, InboxEntryRow.status == "pending", actionable
        )
        queues["schedule_due"] = await _due(session, ScheduleRow.next_fire_at, current, ScheduleRow.state == "enabled")
        queues["schedule_dispatch"] = await _due(
            session,
            func.least(OccurrenceRow.next_attempt_at, OccurrenceRow.deadline_at),
            current,
            OccurrenceRow.status == "pending",
        )
        queues["execution_lease_expired"] = await _due(
            session,
            ToolExecutionRow.lease_expires_at,
            current,
            ToolExecutionRow.completed_at.is_(None),
            ToolExecutionRow.shell.is_(None),
            ToolExecutionRow.mcp_task.is_(None),
        )
        limits: dict[str, int] = {}
        for kind in get_args(OutboxKind.__value__):
            limits[kind] = max(COUNT_LIMIT, policies[kind].backlog_count) if policies is not None else COUNT_LIMIT
            queues[kind] = await _due(
                session,
                OutboxRow.available_at,
                current,
                OutboxRow.kind == kind,
                OutboxRow.status == "pending",
                or_(OutboxRow.lease_expires_at.is_(None), OutboxRow.lease_expires_at <= current),
                limit=limits[kind],
            )
    values = {
        queue: (size, (current - oldest).total_seconds() if oldest is not None else 0)
        for queue, (size, oldest) in queues.items()
    }
    for queue, (size, age) in values.items():
        BACKLOG_SIZE.set(size, {"queue": queue})
        BACKLOG_OLDEST_AGE.set(age, {"queue": queue})
        BACKLOG_LIMIT.set(limits.get(queue, COUNT_LIMIT), {"queue": queue})
        BACKLOG_UPDATED.set(current.timestamp(), {"queue": queue})
    return values


async def _due(
    session: AsyncSession,
    available_at: SQLColumnExpression[datetime | None],
    current: datetime,
    *where: ColumnElement[bool],
    limit: int = COUNT_LIMIT,
) -> tuple[int, datetime | None]:
    """The number of due rows, up to `COUNT_LIMIT`, and the earliest `available_at` among them."""
    due = select(available_at.label("at")).where(*where, available_at <= current).order_by(available_at)
    rows = due.limit(limit).subquery()
    size, oldest = (await session.execute(select(func.count(), func.min(rows.c.at)))).one()
    return size, oldest


logger = get_logger(__name__)
OUTBOX_UPDATED = Gauge(
    "a13n.outbox.updated", unit="s", description="Unix time of last successful outbox policy and dead-state snapshot"
)
OUTBOX_ALERT = Gauge("a13n.outbox.backlog_alert", unit="1", description="Sustained due backlog above its kind policy")
OUTBOX_DEAD = Gauge("a13n.outbox.dead", unit="1", description="Whether a kind has retained dead deliveries")


class BacklogReporter:
    """Report shared gauges and log transitions, with one sustained-backlog timer per kind per process."""

    def __init__(self, storage: Storage, policies: Mapping[OutboxKind, Policy]):
        self.storage, self.policies = storage, policies
        self.since: dict[OutboxKind, datetime] = {}
        self.alerting: set[OutboxKind] = set()
        self.dead: set[OutboxKind] = set()

    async def __call__(self) -> None:
        values = await report_backlog(self.storage, self.policies)
        async with short_session(self.storage) as session:
            current = await now(session)
            dead: set[OutboxKind] = {
                kind
                for kind in self.policies
                if await session.scalar(
                    select(OutboxRow.id).where(OutboxRow.kind == kind, OutboxRow.status == "dead").limit(1)
                )
                is not None
            }
        for kind, policy in self.policies.items():
            size, age = values[kind]
            over = size >= policy.backlog_count or age >= policy.backlog_age_seconds
            if over:
                self.since.setdefault(kind, current)
            else:
                self.since.pop(kind, None)
            alert = over and (current - self.since[kind]).total_seconds() >= policy.backlog_alert_seconds
            OUTBOX_ALERT.set(int(alert), {"kind": kind})
            OUTBOX_DEAD.set(int(kind in dead), {"kind": kind})
            if alert and kind not in self.alerting:
                logger.warning(
                    "Outbox backlog sustained", extra={"kind": kind, "count": size, "oldest_age_seconds": age}
                )
                self.alerting.add(kind)
            elif not alert and kind in self.alerting:
                logger.info("Outbox backlog recovered", extra={"kind": kind})
                self.alerting.remove(kind)
            if kind in dead and kind not in self.dead:
                logger.warning("Outbox has dead deliveries", extra={"kind": kind})
        self.dead = dead
        OUTBOX_UPDATED.set(current.timestamp())
