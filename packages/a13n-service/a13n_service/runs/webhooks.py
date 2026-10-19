"""Lifecycle webhooks: every run or attempt transition stages its deliveries in the same transaction."""

from collections.abc import Sequence
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.subscriptions.delivery import RunFacts, Transition, stage_webhooks
from a13n_service.resources.subscriptions.schemas import LifecycleKind
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules.origin import origins
from a13n_service.runs.schemas import ScheduleOrigin
from a13n_service.runs.tables import AttemptRow, RunRow


def _run(run: RunRow, schedule_origin: ScheduleOrigin | None) -> dict[str, JsonValue]:
    return {
        "id": run.id,
        "schedule_origin": schedule_origin.model_dump(mode="json") if schedule_origin is not None else None,
        "session_id": run.session_id,
        "thread_id": run.thread_id,
        "agent_id": run.agent_id,
        "agent_revision_id": run.agent_revision_id,
        "status": run.status,
        "trigger": run.trigger,
        "wait_reason": run.wait_reason,
        "failure": run.failure,
    }


def _attempt(attempt: AttemptRow) -> dict[str, JsonValue]:
    return {
        "id": attempt.id,
        "number": attempt.number,
        "status": attempt.status,
        "start_reason": attempt.start_reason,
        "yield_reason": attempt.yield_reason,
        "failure": attempt.failure,
    }


async def notify_subscribers(
    session: AsyncSession,
    runtime: Runtime,
    run: RunRow,
    kinds: Sequence[LifecycleKind],
    *,
    at: datetime,
    attempt: AttemptRow | None = None,
) -> None:
    """The last phase of a transition: stage the run's lifecycle payload for every matching subscription."""
    origin = (await origins(session, [run.thread_id])).get(run.thread_id)
    transition = lifecycle_transition(run, kinds, at=at, attempt=attempt, schedule_origin=origin)
    await stage_webhooks(
        session,
        runtime.keys,
        transition.run,
        transition.kinds,
        transition.payload,
        limit=runtime.settings.control.subscriptions,
    )


def lifecycle_transition(
    run: RunRow,
    kinds: Sequence[LifecycleKind],
    *,
    at: datetime,
    attempt: AttemptRow | None = None,
    schedule_origin: ScheduleOrigin | None = None,
) -> Transition:
    """Capture the lifecycle payload after applying the transition."""
    return Transition(
        RunFacts(run.workspace_id, run.agent_id, run.session_id, run.thread_id),
        kinds,
        {
            "occurred_at": at.isoformat(),
            "workspace_id": run.workspace_id,
            "run": _run(run, schedule_origin),
            "attempt": _attempt(attempt) if attempt is not None else None,
        },
    )
