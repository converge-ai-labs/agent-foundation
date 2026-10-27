"""Threads: lookup under tenant scope with the per-thread lock every run write starts from, new thread rows,
and the thread resource's reads and settings."""

from collections.abc import Sequence

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from a13n_service.execution_policy import ExecutionOverride
from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.resources.connections.service import validate_caller_headers
from a13n_service.runs.history import HISTORY, MessageHistory
from a13n_service.runs.schemas import (
    McpHeaders,
    ThreadNavigation,
    ThreadNavigationPage,
    ThreadPage,
    ThreadReference,
    ThreadUpdate,
    ThreadView,
)
from a13n_service.runs.search import predicate
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def get_thread(
    session: AsyncSession, workspace_id: str, thread_id: str, *, lock: bool = False, history: bool = True
) -> ThreadRow:
    query = select(ThreadRow).where(ThreadRow.workspace_id == workspace_id, ThreadRow.id == thread_id)
    if not history:
        query = query.options(defer(ThreadRow.message_history, raiseload=True))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    thread = await session.scalar(query)
    if thread is None:
        raise not_found("thread", thread_id)
    return thread


async def get_run(session: AsyncSession, workspace_id: str, run_id: str, *, lock: bool = False) -> RunRow:
    query = select(RunRow).where(RunRow.workspace_id == workspace_id, RunRow.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await session.scalar(query)
    if run is None:
        raise not_found("run", run_id)
    return run


def require_open(thread: ThreadRow) -> None:
    if thread.purpose == "memory_organization":
        raise conflict("thread", thread.id, "managed_organization")
    if thread.archived_at is not None:
        raise conflict("thread", thread.id, "archived")


def new_thread(
    session_row: SessionRow,
    *,
    mcp_headers: McpHeaders,
    execution: ExecutionOverride | None = None,
    message_history: MessageHistory | None = None,
    origin: str = "new",
    origin_thread_id: str | None = None,
    origin_run_id: str | None = None,
    origin_tool_call_id: str | None = None,
    subagent: str | None = None,
    schedule_occurrence_id: str | None = None,
) -> ThreadRow:
    return ThreadRow(
        id=new_object_id("thread"),
        organization_id=session_row.organization_id,
        workspace_id=session_row.workspace_id,
        session_id=session_row.id,
        schedule_occurrence_id=schedule_occurrence_id,
        origin=origin,
        origin_thread_id=origin_thread_id,
        origin_run_id=origin_run_id,
        origin_tool_call_id=origin_tool_call_id,
        subagent=subagent,
        message_history=HISTORY.dump_python(message_history or [], mode="json"),
        mcp_headers=dict(mcp_headers),
        execution=execution.model_dump(exclude_none=True) if execution is not None else None,
        labels={},
    )


async def refresh_version(session: AsyncSession, thread: ThreadRow) -> ThreadRow:
    """Inbox triggers bump the thread version in SQL; reload it before returning the thread ETag."""
    await session.flush()
    await session.refresh(thread)
    return thread


async def list_threads(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    session_id: str | None,
    label: Sequence[str],
    limit: int,
    cursor: str | None,
    text_query: str | None = None,
    occurrence_id: str | None = None,
    origins: Sequence[str] = (),
    origin_run_id: str | None = None,
) -> ThreadPage:
    """Oldest first, so a session opens at its first thread and child threads follow their parents."""
    match = predicate(RunRow.search_vector, text_query)
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = (
            select(ThreadRow)
            .options(defer(ThreadRow.message_history, raiseload=True))
            .where(
                ThreadRow.workspace_id == scope.workspace_id,
                ThreadRow.purpose == "conversation",
                label_filter(ThreadRow.labels, list(label)),
            )
        )
        if session_id is not None:
            query = query.where(ThreadRow.session_id == session_id)
        if occurrence_id is not None:
            query = query.where(ThreadRow.schedule_occurrence_id == occurrence_id)
        if origins:
            query = query.where(ThreadRow.origin.in_(origins))
        if origin_run_id is not None:
            query = query.where(ThreadRow.origin_run_id == origin_run_id)
        if match is not None:
            query = query.where(
                ThreadRow.id.in_(select(RunRow.thread_id).where(RunRow.workspace_id == scope.workspace_id, match))
            )
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (ThreadRow.created_at, ThreadRow.id),
            kind="threads",
            owner=cursors.query_owner(
                scope.workspace_id, session_id, label, text_query, occurrence_id, sorted(origins), origin_run_id
            ),
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
        return ThreadPage(items=[ThreadView.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get(storage: Storage, actor: Principal, workspace_id: str, thread_id: str) -> ThreadView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return ThreadView.model_validate(await get_thread(session, scope.workspace_id, thread_id, history=False))


async def navigation(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    session_id: str,
    *,
    origins: Sequence[str],
    thread_ids: Sequence[str],
    limit: int,
    cursor: str | None,
) -> ThreadNavigationPage:
    """Read one lightweight page without loading execution settings or message history."""
    owner = cursors.query_owner(workspace_id, session_id, sorted(origins), sorted(thread_ids))
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        if not await session.scalar(
            select(SessionRow.id).where(SessionRow.workspace_id == scope.workspace_id, SessionRow.id == session_id)
        ):
            raise not_found("session", session_id)
        fields = (
            ThreadRow.id,
            ThreadRow.session_id,
            ThreadRow.origin,
            ThreadRow.subagent,
            ThreadRow.origin_thread_id,
            ThreadRow.created_at,
        )
        numbered = (
            select(
                *fields,
                func.row_number()
                .over(partition_by=ThreadRow.origin, order_by=(ThreadRow.created_at, ThreadRow.id))
                .label("origin_number"),
                func.count().over(partition_by=ThreadRow.origin).label("origin_count"),
            )
            .where(
                ThreadRow.workspace_id == scope.workspace_id,
                ThreadRow.session_id == session_id,
                ThreadRow.purpose == "conversation",
            )
            .cte("navigation")
        )
        parent = numbered.alias("parent")
        parent_fields = ("id", "origin", "subagent", "origin_number", "origin_count")
        query = select(numbered, *(parent.c[name].label(f"parent_{name}") for name in parent_fields)).outerjoin(
            parent, parent.c.id == numbered.c.origin_thread_id
        )
        if origins:
            query = query.where(numbered.c.origin.in_(origins))
        if thread_ids:
            query = query.where(numbered.c.id.in_(thread_ids))
        if cursor:
            query = query.where(
                tuple_(numbered.c.created_at, numbered.c.id) > cursors.time_position(cursor, "thread-navigation", owner)
            )
        rows = (
            (await session.execute(query.order_by(numbered.c.created_at, numbered.c.id).limit(limit + 1)))
            .mappings()
            .all()
        )
    items = [
        ThreadNavigation.model_validate(
            {
                **row,
                "origin_thread": ThreadReference.model_validate({name: row[f"parent_{name}"] for name in parent_fields})
                if row["parent_id"]
                else None,
            }
        )
        for row in rows[:limit]
    ]
    return ThreadNavigationPage(
        items=items,
        next_cursor=cursors.encode("thread-navigation", owner, items[-1].created_at.isoformat(), items[-1].id)
        if len(rows) > limit
        else None,
    )


async def update(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, body: ThreadUpdate, *, if_match: str | None
) -> ThreadView:
    """Headers apply to runs accepted afterwards; an accepted run keeps the headers frozen with it."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        if body.labels is not None:
            thread.labels = dict(body.labels)
        if "execution" in body.model_fields_set:
            require_open(thread)
            current = ExecutionOverride.model_validate(thread.execution or {})
            thread.execution = current.patch(body.execution).model_dump(exclude_none=True) if body.execution else None
        if body.mcp_headers is not None:
            require_open(thread)
            await validate_caller_headers(session, scope.workspace_id, body.mcp_headers)
            thread.mcp_headers = dict(body.mcp_headers)
        await session.flush()
        return ThreadView.model_validate(thread)
