"""Threads: lookup under tenant scope with the per-thread lock every run write starts from, new thread rows,
and the thread resource's reads and settings."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.resources.connections.service import validate_caller_headers
from a13n_service.runs.history import HISTORY, MessageHistory
from a13n_service.runs.schemas import McpHeaders, ThreadPage, ThreadUpdate, ThreadView
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def get_thread(session: AsyncSession, workspace_id: str, thread_id: str, *, lock: bool = False) -> ThreadRow:
    query = select(ThreadRow).where(ThreadRow.workspace_id == workspace_id, ThreadRow.id == thread_id)
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
    if thread.archived_at is not None:
        raise conflict("thread", thread.id, "archived")


def new_thread(
    session_row: SessionRow,
    *,
    mcp_headers: McpHeaders,
    message_history: MessageHistory = (),
    origin: str = "new",
    origin_thread_id: str | None = None,
    origin_run_id: str | None = None,
    origin_tool_call_id: str | None = None,
    subagent: str | None = None,
) -> ThreadRow:
    return ThreadRow(
        id=new_object_id("thread"),
        organization_id=session_row.organization_id,
        workspace_id=session_row.workspace_id,
        session_id=session_row.id,
        origin=origin,
        origin_thread_id=origin_thread_id,
        origin_run_id=origin_run_id,
        origin_tool_call_id=origin_tool_call_id,
        subagent=subagent,
        message_history=HISTORY.dump_python(message_history, mode="json"),
        mcp_headers=dict(mcp_headers),
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
) -> ThreadPage:
    """Oldest first, so a session opens at its first thread and child threads follow their parents."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(ThreadRow).where(
            ThreadRow.workspace_id == scope.workspace_id, label_filter(ThreadRow.labels, list(label))
        )
        if session_id is not None:
            query = query.where(ThreadRow.session_id == session_id)
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (ThreadRow.created_at, ThreadRow.id),
            kind="threads",
            owner=cursors.query_owner(scope.workspace_id, session_id, label),
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
        return ThreadPage(items=[ThreadView.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get(storage: Storage, actor: Principal, workspace_id: str, thread_id: str) -> ThreadView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return ThreadView.model_validate(await get_thread(session, scope.workspace_id, thread_id))


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
        if body.mcp_headers is not None:
            require_open(thread)
            await validate_caller_headers(session, scope.workspace_id, body.mcp_headers)
            thread.mcp_headers = dict(body.mcp_headers)
        await session.flush()
        return ThreadView.model_validate(thread)
