"""Workspace-scoped history and explicit retry of failed, unsubmitted events."""

from sqlalchemy import select

from a13n_service.infra import cursors
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.connector_events.schemas import ConnectorEvent as Event
from a13n_service.runs.connector_events.schemas import EventPage
from a13n_service.runs.connector_events.tables import EventRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Scope, allowed_verbs, authorize


def retryable(row: EventRow) -> bool:
    return (
        row.state == "failed"
        and row.inbox_id is None
        and row.snapshot is not None
        and row.payload is not None
        and row.snapshot["target"]["mode"] == "new_thread"
        and row.failure != "event_payload_schema_mismatch"
    )


def view(row: EventRow) -> Event:
    return Event.model_validate(row).model_copy(update={"retryable": retryable(row)})


def require_visible(actor: Principal, row: EventRow) -> None:
    if row.trigger_id is None:
        authorize(actor, Scope(row.organization_id), "admin")


async def list_events(
    runtime: Runtime, actor: Principal, workspace_id: str, *, trigger_id: str | None, limit: int, cursor: str | None
) -> EventPage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(EventRow).where(EventRow.workspace_id == scope.workspace_id)
        if "admin" not in allowed_verbs(actor, Scope(scope.organization_id)):
            query = query.where(EventRow.trigger_id.is_not(None))
        if trigger_id:
            query = query.where(EventRow.trigger_id == trigger_id)
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (EventRow.created_at, EventRow.id),
            kind="connector_events",
            owner=cursors.query_owner(scope.workspace_id, trigger_id or ""),
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return EventPage(items=[view(row) for row in rows], next_cursor=next_cursor)


async def get(runtime: Runtime, actor: Principal, workspace_id: str, event_id: str) -> Event:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, EventRow, scope, event_id, "read")
        require_visible(actor, row)
        return view(row)


async def retry(runtime: Runtime, actor: Principal, workspace_id: str, event_id: str, *, if_match: str | None) -> Event:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        row = await find_row(session, actor, EventRow, scope, event_id, "admin", lock=True)
        require_visible(actor, row)
        require_match(if_match, row.id, row.version)
        if not retryable(row):
            raise conflict(row.KIND, row.id, "event_not_retryable")
        row.state, row.failure, row.attempts, row.due_at = "pending", None, 0, await now(session)
        audit_row(session, actor, row, "retry")
        await session.flush()
        return view(row)
