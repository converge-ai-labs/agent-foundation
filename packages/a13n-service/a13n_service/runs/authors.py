"""Resolve authors only through messages belonging to a readable session."""

from sqlalchemy import select

from a13n_service.infra.db import Storage, short_session
from a13n_service.runs.schemas import MessageAuthor, MessageAuthorQuery, MessageAuthors
from a13n_service.runs.sessions import find_session
from a13n_service.runs.tables import InboxEntryRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.principals import principal_summaries


async def message_authors(
    storage: Storage, actor: Principal, workspace_id: str, session_id: str, query: MessageAuthorQuery
) -> MessageAuthors:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_session(session, scope.workspace_id, session_id)
        rows = (
            await session.execute(
                select(InboxEntryRow.id, InboxEntryRow.principal_id, InboxEntryRow.created_at)
                .join(ThreadRow, ThreadRow.id == InboxEntryRow.thread_id)
                .where(
                    ThreadRow.session_id == session_id,
                    ThreadRow.workspace_id == scope.workspace_id,
                    InboxEntryRow.kind == "message",
                    InboxEntryRow.id.in_(query.entry_id),
                )
                .order_by(InboxEntryRow.id)
            )
        ).all()
        principals = await principal_summaries(session, (row.principal_id for row in rows)) if rows else {}
        return MessageAuthors(
            items=[
                MessageAuthor(
                    entry_id=row.id,
                    principal_id=row.principal_id,
                    submitted_at=row.created_at,
                    principal=principals.get(row.principal_id),
                )
                for row in rows
            ]
        )
