"""Scoped Schedule reads and indexed unfinished-work predicates shared by dispatch and observation."""

from sqlalchemy import Select, and_, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, now, short_session
from a13n_service.infra.errors import not_found
from a13n_service.infra.labels import label_filter
from a13n_service.runs.schedules.lifecycle import rule
from a13n_service.runs.schedules.schemas import (
    BusyOccurrence,
    BusyOccurrencePage,
    Occurrence,
    OccurrenceExecution,
    OccurrencePage,
    OccurrenceQuery,
    OccurrenceSummary,
    Schedule,
    SchedulePage,
    ScheduleQuery,
    ScheduleStatus,
)
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def view(row: ScheduleRow) -> Schedule:
    return Schedule.model_validate(
        {
            "id": row.id,
            "session_id": row.session_id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "description": row.description,
            "labels": row.labels,
            "max_lateness_seconds": row.max_lateness_seconds,
            "invocation": row.invocation,
            "principal_id": row.principal_id,
            "created_by_id": row.created_by_id,
            "updated_by_id": row.updated_by_id,
            "state": row.state,
            "pause_reason": row.pause_reason,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "archived_at": row.archived_at,
            "schedule": rule(row),
            "stream_url": f"/api/v1/sessions/{row.session_id}/stream",
        }
    )


async def find(session: AsyncSession, workspace_id: str, schedule_id: str, *, lock: bool = False) -> ScheduleRow:
    query = select(ScheduleRow).where(ScheduleRow.workspace_id == workspace_id, ScheduleRow.id == schedule_id)
    row = await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None:
        raise not_found("schedule", schedule_id)
    return row


def unfinished_occurrence() -> ColumnElement[bool]:
    """A submitted occurrence ends at its root Thread's first non-waiting terminal Run."""
    finished = exists(
        select(RunRow.id)
        .where(
            RunRow.workspace_id == OccurrenceRow.workspace_id,
            RunRow.thread_id == OccurrenceRow.thread_id,
            RunRow.status.in_(("completed", "failed", "cancelled")),
        )
        .correlate(OccurrenceRow)
    )
    return and_(OccurrenceRow.status == "submitted", ~finished)


async def has_unfinished(session: AsyncSession, row: ScheduleRow) -> bool:
    return bool(
        await session.scalar(
            exists(
                select(OccurrenceRow.id).where(
                    OccurrenceRow.workspace_id == row.workspace_id,
                    OccurrenceRow.schedule_id == row.id,
                    unfinished_occurrence(),
                )
            ).select()
        )
    )


def occurrence_query(row: ScheduleRow, filters: OccurrenceQuery) -> Select[tuple[OccurrenceRow]]:
    query = select(OccurrenceRow).where(
        OccurrenceRow.workspace_id == row.workspace_id, OccurrenceRow.schedule_id == row.id
    )
    if filters.status is not None:
        query = query.where(OccurrenceRow.status == filters.status)
    if filters.trigger is not None:
        query = query.where(OccurrenceRow.trigger == filters.trigger)
    if filters.created_after is not None:
        query = query.where(OccurrenceRow.created_at >= filters.created_after)
    if filters.created_before is not None:
        query = query.where(OccurrenceRow.created_at < filters.created_before)
    if filters.unfinished is not None:
        query = query.where(unfinished_occurrence() if filters.unfinished else ~unfinished_occurrence())
    return query


async def occurrence_page(session: AsyncSession, row: ScheduleRow, filters: OccurrenceQuery) -> OccurrencePage:
    rows, cursor = await cursors.keyset_page(
        session,
        occurrence_query(row, filters),
        (OccurrenceRow.created_at, OccurrenceRow.id),
        kind="schedule_occurrences",
        owner=cursors.query_owner(
            f"{row.workspace_id}:{row.id}", filters.model_dump(mode="json", exclude={"limit", "cursor", "include"})
        ),
        cursor=filters.cursor,
        limit=filters.limit,
        newest_first=True,
    )
    items = [OccurrenceSummary.model_validate(item) for item in rows]
    if filters.include == "execution":
        executions = await execution_summaries(session, row.workspace_id, row.id, [item.id for item in items])
        items = [item.model_copy(update={"execution": executions[item.id]}) for item in items]
    return OccurrencePage(items=items, next_cursor=cursor)


async def execution_summaries(
    session: AsyncSession, workspace_id: str, schedule_id: str, occurrence_ids: list[str]
) -> dict[str, OccurrenceExecution]:
    """Read only the page's compact facts in one statement, including the shared occurrence completion predicate."""
    if not occurrence_ids:
        return {}
    rows = await session.execute(
        select(
            OccurrenceRow.id,
            InboxEntryRow.status.label("entry_status"),
            RunRow.id.label("run_id"),
            RunRow.status.label("run_status"),
            unfinished_occurrence().label("unfinished"),
            func.statement_timestamp().label("observed_at"),
        )
        .select_from(OccurrenceRow)
        .outerjoin(
            InboxEntryRow,
            and_(
                InboxEntryRow.workspace_id == OccurrenceRow.workspace_id,
                InboxEntryRow.thread_id == OccurrenceRow.thread_id,
                InboxEntryRow.id == OccurrenceRow.entry_id,
            ),
        )
        .outerjoin(
            RunRow,
            and_(
                RunRow.workspace_id == OccurrenceRow.workspace_id,
                RunRow.thread_id == OccurrenceRow.thread_id,
                RunRow.id == InboxEntryRow.assigned_run_id,
            ),
        )
        .where(
            OccurrenceRow.workspace_id == workspace_id,
            OccurrenceRow.schedule_id == schedule_id,
            OccurrenceRow.id.in_(occurrence_ids),
        )
    )
    return {
        row.id: OccurrenceExecution.model_validate(
            {
                "initial_entry_status": row.entry_status,
                "initial_run": {"id": row.run_id, "status": row.run_status} if row.run_id is not None else None,
                "has_unfinished_work": row.unfinished,
                "observed_at": row.observed_at,
            }
        )
        for row in rows
    }


async def progress(session: AsyncSession, row: ScheduleRow) -> ScheduleStatus:
    page = await occurrence_page(session, row, OccurrenceQuery(unfinished=True, limit=20))
    return ScheduleStatus(
        schedule_id=row.id,
        session_id=row.session_id,
        observed_at=await now(session),
        next_fire_at=row.next_fire_at,
        pending_occurrence_id=row.pending_occurrence_id,
        last_occurrence_id=row.last_occurrence_id,
        has_unfinished_work=bool(page.items),
        busy_occurrences=BusyOccurrencePage(
            items=[
                BusyOccurrence(occurrence_id=item.id, root_thread_id=item.thread_id, entry_id=item.entry_id)
                for item in page.items
                if item.thread_id is not None and item.entry_id is not None
            ],
            next_cursor=page.next_cursor,
        ),
    )


async def get(storage: Storage, actor: Principal, workspace_id: str, schedule_id: str) -> Schedule:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return view(await find(session, scope.workspace_id, schedule_id))


async def list_schedules(storage: Storage, actor: Principal, workspace_id: str, filters: ScheduleQuery) -> SchedulePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(ScheduleRow).where(
            ScheduleRow.workspace_id == scope.workspace_id, label_filter(ScheduleRow.labels, filters.label)
        )
        query = query.where(
            ScheduleRow.state == filters.state if filters.state is not None else ScheduleRow.state != "archived"
        )
        if filters.agent_id:
            query = query.where(ScheduleRow.invocation["agent_id"].astext == filters.agent_id)
        if filters.principal_id:
            query = query.where(ScheduleRow.principal_id == filters.principal_id)
        if filters.q:
            query = query.where(
                or_(
                    ScheduleRow.name.icontains(filters.q, autoescape=True),
                    ScheduleRow.description.icontains(filters.q, autoescape=True),
                )
            )
        rows, cursor = await cursors.keyset_page(
            session,
            query,
            (ScheduleRow.created_at, ScheduleRow.id),
            kind="schedules",
            owner=cursors.query_owner(scope.workspace_id, filters.model_dump(mode="json", exclude={"limit", "cursor"})),
            cursor=filters.cursor,
            limit=filters.limit,
            newest_first=True,
        )
        return SchedulePage(items=[view(row) for row in rows], next_cursor=cursor)


async def status(storage: Storage, actor: Principal, workspace_id: str, schedule_id: str) -> ScheduleStatus:
    async with short_session(storage) as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return await progress(session, await find(session, scope.workspace_id, schedule_id))


async def list_occurrences(
    storage: Storage, actor: Principal, workspace_id: str, schedule_id: str, filters: OccurrenceQuery
) -> OccurrencePage:
    async with short_session(storage) as session:
        if filters.include == "execution":
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return await occurrence_page(session, await find(session, scope.workspace_id, schedule_id), filters)


async def get_occurrence(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    schedule_id: str,
    occurrence_id: str,
    *,
    execution: bool = False,
) -> Occurrence:
    async with short_session(storage) as session:
        if execution:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await session.scalar(
            select(OccurrenceRow).where(
                OccurrenceRow.workspace_id == scope.workspace_id,
                OccurrenceRow.schedule_id == schedule_id,
                OccurrenceRow.id == occurrence_id,
            )
        )
        if row is None:
            raise not_found("schedule_occurrence", occurrence_id)
        result = Occurrence.model_validate(row)
        if execution:
            summaries = await execution_summaries(session, scope.workspace_id, schedule_id, [row.id])
            result = result.model_copy(update={"execution": summaries[row.id]})
        return result
