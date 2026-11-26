"""Native Schedules that start a fresh Thread at each occurrence."""

from collections.abc import Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.app_provenance import AppProvenance
from a13n_service.infra import cursors
from a13n_service.infra.errors import conflict, invalid
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
    app_context: dict[str, str] | None = None,
) -> str:
    """Use an existing conversation Session or create one with Session Memory off by default."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    provenance = AppProvenance(app_id, app_context)
    if existing_session_id is not None:
        row = await find_session(session, workspace_id, existing_session_id)
        require_conversation(row)
        if not provenance.matches_owner(row):
            raise conflict("session", row.id, "app_owner_mismatch")
        return row.id
    body = session_create
    row = new_session(scope.organization_id, workspace_id, actor.id, body, provenance=provenance)
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
    app_context: dict[str, str] | None = None,
) -> tuple[Schedule, bool]:
    """Create an App-owned Schedule with a fresh Thread target."""
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
        body,
        request_key=request_key,
        provenance=AppProvenance(app_id, app_context),
    )


def _fresh(workspace_id: str, app_id: str, labels: Mapping[str, str] | None, app_context: dict[str, str] | None):
    predicates = [
        ScheduleRow.workspace_id == workspace_id,
        ScheduleRow.thread_id.is_(None),
        ScheduleRow.app_id == app_id,
    ]
    if labels:
        predicates.append(ScheduleRow.labels.contains(dict(labels)))
    if app_context is not None:
        predicates.append(ScheduleRow.app_context.contains(AppProvenance(app_id, app_context).app_context))
    return predicates


async def list_app_schedules(
    session: AsyncSession,
    workspace_id: str,
    *,
    app_id: str,
    labels: Mapping[str, str] | None = None,
    app_context: dict[str, str] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> SchedulePage:
    """Page fresh Schedules by App and optional instance labels."""
    if not 1 <= limit <= 200:
        raise invalid("limit", "must_be_between_1_and_200")
    rows, next_cursor = await cursors.keyset_page(
        session,
        select(ScheduleRow).where(*_fresh(workspace_id, app_id, labels, app_context)),
        (ScheduleRow.created_at, ScheduleRow.id),
        kind="app_fresh_schedules",
        owner=(
            cursors.query_owner(
                workspace_id, app_id, sorted((labels or {}).items()), sorted((app_context or {}).items())
            )
        ),
        cursor=cursor,
        limit=limit,
        newest_first=True,
    )
    return SchedulePage(items=[queries.view(row) for row in rows], next_cursor=next_cursor)


async def count_active_fresh_schedules(
    session: AsyncSession,
    workspace_id: str,
    *,
    app_id: str,
    labels: Mapping[str, str] | None = None,
    app_context: dict[str, str] | None = None,
) -> int:
    """Count enabled or paused fresh Schedules, plus any with a pending occurrence."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(ScheduleRow)
            .where(
                *_fresh(workspace_id, app_id, labels, app_context),
                or_(ScheduleRow.state.in_(("enabled", "paused")), ScheduleRow.pending_occurrence_id.is_not(None)),
            )
        )
        or 0
    )
