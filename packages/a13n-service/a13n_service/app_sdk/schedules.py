"""Native Schedules that start a fresh Thread at each occurrence."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.errors import invalid
from a13n_service.runs.memories.session_memory import configure
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schedules import queries, service
from a13n_service.runs.schedules.schemas import Schedule, ScheduleCreate, SchedulePage
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.schemas import SessionCreate, SessionMemorySettings
from a13n_service.runs.sessions import find_session, new_session, require_conversation
from a13n_service.tenancy.authorize import authorize

from ._access import app_actor, app_workspace, run_actor


async def ensure_schedule_session(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    principal_id: str,
    existing_session_id: str | None,
    session_create: SessionCreate,
) -> str:
    """Use an existing conversation Session or create one with Session Memory off by default."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    if existing_session_id is not None:
        row = await find_session(session, workspace_id, existing_session_id)
        require_conversation(row)
        return row.id
    body = session_create
    row = new_session(scope.organization_id, workspace_id, actor.id, body)
    session.add(row)
    await session.flush()
    await configure(session, row, body.memory or SessionMemorySettings(enabled=False))
    await session.flush()
    return row.id


async def create_fresh_schedule(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    body: ScheduleCreate,
    request_key: str,
) -> tuple[Schedule, bool]:
    """Create a native Schedule with a fresh Thread target and an enforced App label."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await app_actor(session, runtime, scope, actor_id)
    authorize(actor, scope, "write")
    if body.target.mode != "new_thread":
        raise invalid("target.mode", "requires_new_thread_target")
    return await service.create_in_transaction(
        session,
        runtime,
        actor,
        scope,
        ScheduleCreate.model_validate({**body.model_dump(), "labels": {**body.labels, "app": app_id}}),
        request_key=request_key,
    )


def _fresh(workspace_id: str, app_id: str):
    return (
        ScheduleRow.workspace_id == workspace_id,
        ScheduleRow.thread_id.is_(None),
        ScheduleRow.labels.contains({"app": app_id}),
    )


async def list_app_schedules(
    session: AsyncSession,
    workspace_id: str,
    *,
    app_id: str,
    limit: int = 50,
    cursor: str | None = None,
) -> SchedulePage:
    """Page fresh Schedules labeled for an App-authorized workspace."""
    if not 1 <= limit <= 200:
        raise invalid("limit", "must_be_between_1_and_200")
    rows, next_cursor = await cursors.keyset_page(
        session,
        select(ScheduleRow).where(*_fresh(workspace_id, app_id)),
        (ScheduleRow.created_at, ScheduleRow.id),
        kind="app_fresh_schedules",
        owner=cursors.query_owner(workspace_id, app_id),
        cursor=cursor,
        limit=limit,
        newest_first=True,
    )
    return SchedulePage(items=[queries.view(row) for row in rows], next_cursor=next_cursor)


async def count_active_fresh_schedules(session: AsyncSession, workspace_id: str, *, app_id: str) -> int:
    """Count fresh Schedules that have not reached a terminal state."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(ScheduleRow)
            .where(
                *_fresh(workspace_id, app_id),
                ScheduleRow.state.in_(("enabled", "paused")),
            )
        )
        or 0
    )
