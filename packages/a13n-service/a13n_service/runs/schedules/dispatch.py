"""Durable manual intent and atomic submission through the ordinary Thread/inbox path."""

from datetime import timedelta
from functools import partial

from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import advisory_lock, after_commit, now, transaction
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, RETRY_AFTER, ServiceError, conflict
from a13n_service.infra.http import require_match
from a13n_service.runs.accept import accept
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import lifecycle, queries, service, webhooks
from a13n_service.runs.schedules.schemas import Occurrence, OccurrenceSnapshot
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.schemas import NewThread
from a13n_service.runs.submit import prepare_thread
from a13n_service.tenancy.access import principal_for, require_active_workspace, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize, execution_authority

logger = get_logger(__name__)


async def manual(
    runtime: Runtime, actor: Principal, workspace_id: str, schedule_id: str, *, request_key: str, if_match: str | None
) -> tuple[Occurrence, bool]:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        # Request keys are unique across this requester's schedules, not merely within one Schedule.
        await advisory_lock(session, "schedule.dispatch", scope.workspace_id, actor.id, request_key)
        row = await queries.find(session, scope.workspace_id, schedule_id, lock=True)
        service.require_owner(row, actor)
        saved = ExecutionAuthority.model_validate(row.authority)
        authorize(actor, scope, "run", authority=saved)
        digest = service.request_digest("schedule.dispatch", row.id, {})
        found = await session.scalar(
            select(OccurrenceRow).where(
                OccurrenceRow.workspace_id == scope.workspace_id,
                OccurrenceRow.requested_by_id == actor.id,
                OccurrenceRow.request_key == request_key,
            )
        )
        if found is not None:
            if found.request_digest != digest:
                raise conflict("request", request_key, IDEMPOTENCY_KEY_REUSED)
            return Occurrence.model_validate(found), False
        require_match(if_match, row.id, row.version)
        service.require_editable(row)
        if row.pending_occurrence_id is not None or await queries.has_unfinished(session, row):
            raise conflict("schedule", row.id, "schedule_busy")
        current = await now(session)
        authority = saved.model_copy(update={"verbs": saved.verbs & execution_authority(actor, scope).verbs})
        item = await lifecycle.occurrence(
            session,
            runtime,
            row,
            current,
            trigger="manual",
            requested_by_id=actor.id,
            request_key=request_key,
            request_digest=digest,
            authority=authority,
        )
        record(
            session,
            scope,
            actor_id=actor.id,
            action="schedule.dispatch",
            target_kind="schedule",
            target_id=row.id,
            details={"session_id": row.session_id, "occurrence_id": item.id},
        )
        await session.flush()
        return Occurrence.model_validate(item), True


async def _log(details: dict[str, object]) -> None:
    logger.info("Schedule occurrence processed", extra=details)


def log_after_commit(
    session: AsyncSession, row: ScheduleRow, item: OccurrenceRow, *, run_id: str | None = None
) -> None:
    delay = ((item.finished_at or item.created_at) - item.scheduled_at).total_seconds() if item.scheduled_at else None
    after_commit(
        session,
        partial(
            _log,
            {
                "schedule_id": row.id,
                "schedule_type": row.schedule_type,
                "schedule_state": row.state,
                "occurrence_id": item.id,
                "status": item.status,
                "reason": item.reason,
                "thread_id": item.thread_id,
                "entry_id": item.entry_id,
                "scheduled_at": item.scheduled_at,
                "run_id": run_id,
                "delay_seconds": delay,
            },
        ),
    )


async def process(runtime: Runtime, schedule_id: str) -> None:
    """One pending submission; competing scans skip the locked Schedule. A failed transaction leaves intent intact."""
    occurrence_id = None
    try:
        async with transaction(runtime.storage) as session:
            row = await session.scalar(
                select(ScheduleRow).where(ScheduleRow.id == schedule_id).with_for_update(skip_locked=True)
            )
            if row is None or row.pending_occurrence_id is None:
                return
            item = await session.get_one(OccurrenceRow, row.pending_occurrence_id, with_for_update=True)
            if item.status != "pending":
                return
            current = await now(session)
            if current >= item.deadline_at:
                await lifecycle.finish(session, runtime, row, item, current, status="skipped", reason="missed_deadline")
                log_after_commit(session, row, item)
                return
            if item.next_attempt_at is not None and item.next_attempt_at > current:
                return
            if await queries.has_unfinished(session, row):
                await lifecycle.finish(session, runtime, row, item, current, status="skipped", reason="overlap")
                log_after_commit(session, row, item)
                return
            occurrence_id = item.id
            scope = WorkspaceScope(row.organization_id, row.workspace_id)
            authority = ExecutionAuthority.model_validate(item.authority)
            actor = await principal_for(session, runtime.access, item.principal_id, confinement=scope)
            await require_active_workspace(session, row.workspace_id)
            authorize(actor, scope, "run", authority=authority)
            invocation = OccurrenceSnapshot.model_validate(item.snapshot).invocation
            thread, source = await prepare_thread(
                session,
                runtime,
                actor,
                scope,
                NewThread(session_id=row.session_id, **invocation.model_dump()),
                request=None,
                authority=authority,
                schedule_occurrence_id=item.id,
            )
            await accept(session, runtime, thread, explicit=source)
            assert source.entry is not None
            item.dispatch_attempts += 1
            item.thread_id, item.entry_id = thread.id, source.entry.id
            await lifecycle.finish(session, runtime, row, item, await now(session), status="submitted", reason=None)
            await session.flush()
            log_after_commit(session, row, item, run_id=source.entry.assigned_run_id)
    except (ServiceError, DBAPIError) as error:
        if occurrence_id is None:
            raise
        # Roll back candidates and their after-commit callbacks together. Re-read first: a lost commit response
        # may already have left a submitted input, or a concurrent pause may have cancelled this intent.
        await _failed_attempt(runtime, schedule_id, occurrence_id, error)


async def _failed_attempt(
    runtime: Runtime, schedule_id: str, occurrence_id: str, error: ServiceError | DBAPIError
) -> None:
    async with transaction(runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule_id, with_for_update=True)
        item = await session.get_one(OccurrenceRow, occurrence_id, with_for_update=True)
        if item.status != "pending":
            return
        current = await now(session)
        item.dispatch_attempts += 1
        # Error codes are safe to retain; exception messages and provider details may contain inputs or secrets.
        code = error.code if isinstance(error, ServiceError) else "database_conflict"
        item.last_error = code
        retryable = isinstance(error, DBAPIError) or code in {"unavailable", "rate_limited", "request_timeout"}
        if current >= item.deadline_at:
            await lifecycle.finish(session, runtime, row, item, current, status="skipped", reason="missed_deadline")
        elif retryable and item.dispatch_attempts < runtime.settings.schedules.dispatch_max_attempts:
            retry_after = error.details.get(RETRY_AFTER, 0) if isinstance(error, ServiceError) else 0
            delay = max(
                5 * 2 ** (item.dispatch_attempts - 1), retry_after if isinstance(retry_after, (int, float)) else 0
            )
            item.next_attempt_at = current + timedelta(seconds=delay)
        elif retryable:
            await lifecycle.finish(
                session, runtime, row, item, current, status="failed", reason="dispatch_attempts_exhausted"
            )
        else:
            reason = "authority_invalid" if code in {"forbidden", "unauthenticated"} else "target_invalid"
            await lifecycle.finish(session, runtime, row, item, current, status="failed", reason=reason)
            if row.state != "completed":
                await lifecycle.close_due(session, runtime, row, current, reason)
                previous_state = (row.state, row.pause_reason)
                row.state, row.pause_reason, row.next_fire_at = "paused", reason, None
                if previous_state != (row.state, row.pause_reason):
                    await webhooks.schedule_changed(session, runtime, row, current)
                record(
                    session,
                    WorkspaceScope(row.organization_id, row.workspace_id),
                    actor_id=None,
                    action="schedule.auto_pause",
                    target_kind="schedule",
                    target_id=row.id,
                    details={
                        "principal_id": row.principal_id,
                        "session_id": row.session_id,
                        "occurrence_id": item.id,
                        "reason": reason,
                    },
                )
        await session.flush()
        log_after_commit(session, row, item)
