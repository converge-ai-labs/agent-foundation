"""Indexed discovery of active display work, finite recovery, and overdue cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import case, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, optional_assume_utc

from .recovery import DISPLAY_RECOVERY_WINDOW

type DisplayLane = Literal["active", "recovery", "cleanup"]

_TERMINAL_EVENTS = ("run.waiting", "run.completed", "run.failed", "run.cancelled")


@dataclass(frozen=True, slots=True)
class DisplayCandidate:
    organization_id: str
    run_id: str
    thread_id: str
    sealed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DisplaySettlement:
    accepted_projected: bool
    closed_at: datetime | None
    abandoned: bool
    lifecycle_missing: bool = False


class DisplayCandidates:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def page(
        self, *, lane: DisplayLane, after: DisplayCandidate | None, limit: int, now: datetime
    ) -> tuple[DisplayCandidate, ...]:
        run = RunRecord
        query = select(run.organization_id, run.id, run.thread_id, run.sealed_at).limit(limit)
        if lane == "active":
            query = query.where(run.sealed_at.is_(None)).order_by(run.id)
            if after is not None:
                query = query.where(run.id > after.run_id)
        else:
            cutoff = now - DISPLAY_RECOVERY_WINDOW
            query = query.where(run.sealed_at.is_not(None), run.display_settled_at.is_(None))
            query = query.where(run.sealed_at > cutoff if lane == "recovery" else run.sealed_at <= cutoff)
            query = query.where(or_(run.display_next_attempt_at.is_(None), run.display_next_attempt_at <= now))
            query = query.order_by(run.sealed_at, run.id)
            if after is not None:
                query = query.where(tuple_(run.sealed_at, run.id) > (after.sealed_at, after.run_id))
        async with short_session(self._sessions) as database:
            rows = (await database.execute(query)).all()
        return tuple(
            DisplayCandidate(org, run_id, thread, optional_assume_utc(sealed)) for org, run_id, thread, sealed in rows
        )

    async def settle(self, candidate: DisplayCandidate, *, now: datetime) -> None:
        async with transaction(self._sessions) as database:
            await database.execute(
                update(RunRecord)
                .where(
                    RunRecord.organization_id == candidate.organization_id,
                    RunRecord.id == candidate.run_id,
                    RunRecord.sealed_at.is_not(None),
                    RunRecord.display_settled_at.is_(None),
                )
                .values(display_settled_at=now, display_next_attempt_at=None)
            )

    async def retry_after(self, candidate: DisplayCandidate, *, when: datetime) -> None:
        async with transaction(self._sessions) as database:
            await database.execute(
                update(RunRecord)
                .where(
                    RunRecord.organization_id == candidate.organization_id,
                    RunRecord.id == candidate.run_id,
                    RunRecord.sealed_at.is_not(None),
                    RunRecord.display_settled_at.is_(None),
                )
                .values(display_next_attempt_at=when)
            )

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
            run = await database.get(RunRecord, candidate.run_id)
        if unsettled:
            return DisplaySettlement(bool(accepted), None, bool(abandoned))
        missing = not accepted and not abandoned
        if terminal_at is None and run is not None and run.organization_id == candidate.organization_id:
            if run.status in {"waiting", "completed", "failed", "cancelled"} and run.sealed_at is not None:
                terminal_at = assume_utc(run.sealed_at)
                missing = True
        return DisplaySettlement(bool(accepted), terminal_at, bool(abandoned), missing)
