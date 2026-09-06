"""Cooperative cancellation propagation for terminated parent Runs."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_harness import SafeFailure
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import WorkspaceAction
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.storage import short_session

from .authorization import ChildRunAuthorizationError, authorize_parent_child_action
from .domain import ChildCancellationPolicy
from .models import ChildRunRelationshipRecord

_PARENT_CANCELLATION_OUTCOMES = {RunStatus.failed.value, RunStatus.cancelled.value}
_ACTIVE_CHILD_OUTCOMES = {RunStatus.accepted.value, RunStatus.running.value}


class ChildCancellationError(RuntimeError):
    """A bounded failure while applying a relationship cancellation policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChildCancellationBatch:
    parent_run_id: str
    scanned: int
    cancelled: int
    conflicted: int
    next_after_child_thread_id: str | None


@dataclass(frozen=True, slots=True)
class _CancellationCandidate:
    run_id: str
    thread_id: str
    run_version: int
    thread_version: int


class ChildCancellationReconciler:
    """Cancel active child heads whose relationship explicitly requests propagation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        outcomes: RunOutcomeService,
    ) -> None:
        self._sessions = sessions
        self._outcomes = outcomes

    async def reconcile_parent(
        self,
        *,
        organization_id: str,
        parent_run_id: str,
        after_child_thread_id: str | None = None,
        limit: int = 100,
    ) -> ChildCancellationBatch:
        """Process one stable child-Thread page without holding a session across cancellation calls."""

        if not 1 <= limit <= 1000:
            raise ValueError("child cancellation batch limit must be between 1 and 1000")
        candidates = await self._read_authorized_candidates(
            organization_id=organization_id,
            parent_run_id=parent_run_id,
            after_child_thread_id=after_child_thread_id,
            limit=limit,
        )
        cancelled = 0
        conflicted = 0
        for candidate in candidates:
            try:
                await self._outcomes.cancel(
                    organization_id=organization_id,
                    run_id=candidate.run_id,
                    expected_run_version=candidate.run_version,
                    expected_thread_version=candidate.thread_version,
                    failure=SafeFailure(
                        code="subagent_parent_terminated",
                        message="The parent Run requested cooperative child cancellation.",
                    ),
                )
            except RunOutcomeError:
                conflicted += 1
            else:
                cancelled += 1
        next_cursor = candidates[-1].thread_id if len(candidates) == limit else None
        return ChildCancellationBatch(
            parent_run_id=parent_run_id,
            scanned=len(candidates),
            cancelled=cancelled,
            conflicted=conflicted,
            next_after_child_thread_id=next_cursor,
        )

    async def _read_authorized_candidates(
        self,
        *,
        organization_id: str,
        parent_run_id: str,
        after_child_thread_id: str | None,
        limit: int,
    ) -> tuple[_CancellationCandidate, ...]:
        async with short_session(self._sessions) as database:
            parent = await database.scalar(
                select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id == parent_run_id)
            )
            if parent is None:
                raise ChildCancellationError(
                    "subagent_parent_missing",
                    "Parent Run was not found for child cancellation reconciliation",
                )
            if parent.status not in _PARENT_CANCELLATION_OUTCOMES:
                return ()
            session = await _require_session(database, parent)
            statement = (
                select(ThreadRecord, RunRecord)
                .join(
                    ChildRunRelationshipRecord,
                    and_(
                        ChildRunRelationshipRecord.organization_id == ThreadRecord.organization_id,
                        ChildRunRelationshipRecord.child_thread_id == ThreadRecord.id,
                    ),
                )
                .join(
                    RunRecord,
                    and_(
                        RunRecord.organization_id == ThreadRecord.organization_id,
                        RunRecord.id == ChildRunRelationshipRecord.child_run_id,
                        RunRecord.id == ThreadRecord.current_run_id,
                    ),
                )
                .where(
                    ChildRunRelationshipRecord.organization_id == organization_id,
                    ChildRunRelationshipRecord.parent_run_id == parent_run_id,
                    ChildRunRelationshipRecord.cancellation_policy
                    == ChildCancellationPolicy.request_child_cancel.value,
                    RunRecord.status.in_(_ACTIVE_CHILD_OUTCOMES),
                )
                .order_by(ThreadRecord.id)
                .limit(limit)
            )
            if after_child_thread_id is not None:
                statement = statement.where(ThreadRecord.id > after_child_thread_id)
            rows = tuple((await database.execute(statement)).all())
            try:
                await authorize_parent_child_action(
                    database,
                    parent=parent.to_resource(),
                    child_agent_ids=tuple(sorted({run.agent_id for _, run in rows})),
                    workspace_id=session.workspace_id,
                    action=WorkspaceAction.run_interrupt,
                )
            except ChildRunAuthorizationError as error:
                raise ChildCancellationError(
                    "subagent_cancellation_authorization_denied",
                    "Persisted parent Principal is no longer authorized to cancel its child Runs",
                ) from error
            return tuple(
                _CancellationCandidate(
                    run_id=run.id,
                    thread_id=thread.id,
                    run_version=run.version,
                    thread_version=thread.version,
                )
                for thread, run in rows
            )


async def _require_session(database: AsyncSession, parent: RunRecord) -> SessionRecord:
    session = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.organization_id == parent.organization_id,
            SessionRecord.id == parent.session_id,
        )
    )
    if session is None:
        raise ChildCancellationError(
            "subagent_parent_session_missing",
            "Parent Session was not found for child cancellation reconciliation",
        )
    return session


__all__ = [
    "ChildCancellationBatch",
    "ChildCancellationError",
    "ChildCancellationReconciler",
]
