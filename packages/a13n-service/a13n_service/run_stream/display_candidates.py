"""Bounded, fair Run discovery; SQL owns identity and lifecycle settlement only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session

_TERMINAL_EVENTS = ("run.waiting", "run.completed", "run.failed", "run.cancelled")


@dataclass(frozen=True, slots=True)
class DisplayCandidate:
    organization_id: str
    run_id: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class DisplaySettlement:
    accepted_projected: bool
    closed_at: datetime | None
    abandoned: bool


class DisplayCandidates:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def page(self, *, after_run_id: str | None, limit: int) -> tuple[DisplayCandidate, ...]:
        # Every retained Run is revisited, including terminal Runs after restart.
        # Keyset traversal prevents a busy prefix from starving later identities.
        query = select(RunRecord.organization_id, RunRecord.id, RunRecord.thread_id).order_by(RunRecord.id).limit(limit)
        if after_run_id is not None:
            query = query.where(RunRecord.id > after_run_id)
        async with short_session(self._sessions) as database:
            rows = (await database.execute(query)).all()
        return tuple(DisplayCandidate(*row) for row in rows)

    async def settlement(self, candidate: DisplayCandidate) -> DisplaySettlement:
        fact = LifecycleEventRecord
        query = select(
            func.count(case(((fact.event_type == "run.accepted") & (fact.projection_state == "projected"), 1))),
            func.max(case((fact.event_type.in_(_TERMINAL_EVENTS), fact.occurred_at))),
            func.count(case((fact.projection_state.not_in(("projected", "abandoned")), 1))),
            func.count(case((fact.projection_state == "abandoned", 1))),
        ).where(fact.organization_id == candidate.organization_id, fact.run_id == candidate.run_id)
        async with short_session(self._sessions) as database:
            accepted, terminal_at, unsettled, abandoned = (await database.execute(query)).one()
        return DisplaySettlement(bool(accepted), terminal_at if not unsettled else None, bool(abandoned))
