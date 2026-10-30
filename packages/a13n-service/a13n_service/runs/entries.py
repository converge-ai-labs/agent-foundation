"""Reading a thread's inbox and changing what is still pending in it.

Edits, withdrawal and reordering use the thread ETag, because each changes the thread's visible inbox.
None of them can make a pending entry eligible, so none tries acceptance. Anyone who may run in the thread
may withdraw or reorder its input; only an entry's own principal may edit it.
"""

from collections.abc import Sequence

from sqlalchemy import select

from a13n_service.infra import cursors, reads
from a13n_service.infra.db import Storage, now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import require_match
from a13n_service.runs import inbox, read_views
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EntryPage,
    EntryStatus,
    EntryUpdate,
    EntryView,
    InboxOrder,
    Submitted,
    ThreadView,
)
from a13n_service.runs.submit import receipt, validate_message
from a13n_service.runs.tables import InboxEntryRow
from a13n_service.runs.threads import get_thread, refresh_version
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal


async def list_entries(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    *,
    status: Sequence[EntryStatus],
    limit: int,
    cursor: str | None,
    view: reads.ReadView = "detailed",
    kind: str | None = None,
) -> EntryPage | read_views.EntrySummaryPage:
    """In inbox order, which is also the order pending entries become runs."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        query = select(InboxEntryRow).where(InboxEntryRow.thread_id == thread.id)
        if status:
            query = query.where(InboxEntryRow.status.in_(status))
        if kind is not None:
            query = query.where(InboxEntryRow.kind == kind)
        owner = cursors.query_owner(thread.id, status) if kind is None else cursors.query_owner(thread.id, status, kind)
        if cursor is not None:
            position = cursors.decode(cursor, "inbox", owner)
            if len(position) != 1 or not isinstance(position[0], int):
                raise ServiceError("invalid_cursor", "Invalid collection cursor")
            query = query.where(InboxEntryRow.position > position[0])
        rows = (await session.scalars(query.order_by(InboxEntryRow.position).limit(limit + 1))).all()
        if view == "compact":
            values = [read_views.entry_summary(EntryView.model_validate(row)) for row in rows[:limit]]
            count = reads.fit_count(values)
            return read_views.EntrySummaryPage(
                items=values[:count],
                next_cursor=cursors.encode("inbox", owner, rows[count - 1].position) if len(rows) > count else None,
            )
        return EntryPage(
            items=[EntryView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("inbox", owner, rows[limit - 1].position) if len(rows) > limit else None,
        )


async def get_entry(storage: Storage, actor: Principal, workspace_id: str, thread_id: str, entry_id: str) -> EntryView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        return EntryView.model_validate(await inbox.get_entry(session, thread, entry_id))


async def edit(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    entry_id: str,
    body: EntryUpdate,
    *,
    if_match: str | None,
) -> Submitted:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        entry = await inbox.editable_entry(session, thread, entry_id, editor_id=actor.id)
        message = inbox.edited(entry, body)
        authority = ExecutionAuthority.model_validate(entry.authority)
        await validate_message(session, runtime, actor, scope, thread, message, authority=authority)
        await inbox.edit_entry(session, thread, entry, message, control=runtime.settings.control)
        runtime.mailbox.notify(session, entry)
        return await receipt(session, thread, entry)


async def withdraw(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, entry_id: str, *, if_match: str | None
) -> Submitted:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        entry = await inbox.withdraw_entry(session, thread, entry_id, at=await now(session))
        return await receipt(session, thread, entry)


async def reorder(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, body: InboxOrder, *, if_match: str | None
) -> ThreadView:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        await inbox.reorder(session, thread, body.entry_ids)
        return ThreadView.model_validate(await refresh_version(session, thread))
