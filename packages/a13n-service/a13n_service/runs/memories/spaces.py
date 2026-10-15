"""Space execution status, organization requests, Session associations and coordinated deletion."""

from sqlalchemy import select

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict, disabled
from a13n_service.infra.http import require_match
from a13n_service.resources.memories.schemas import OrganizationPolicy
from a13n_service.resources.memories.tables import MemoryRow, MemorySpaceRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from a13n_service.runs.runs import run_views
from a13n_service.runs.schemas import RunPage, SessionPage, SpaceOrganizationStatus
from a13n_service.runs.sessions import _views as session_views
from a13n_service.runs.tables import RunRow, SessionRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def status_view(state: SpaceOrganizationRow | None) -> SpaceOrganizationStatus:
    return (
        SpaceOrganizationStatus()
        if state is None
        else SpaceOrganizationStatus.model_validate(state, from_attributes=True)
    )


async def get_status(storage: Storage, actor: Principal, workspace_id: str, space_id: str) -> SpaceOrganizationStatus:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_row(session, actor, MemorySpaceRow, scope, space_id, "read")
        return status_view(await session.get(SpaceOrganizationRow, space_id))


async def request_organization(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    space_id: str,
) -> SpaceOrganizationStatus:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        space = await find_row(session, actor, MemorySpaceRow, scope, space_id, "run", lock=True)
        if not space.enabled:
            raise disabled(MemorySpaceRow.KIND, space.id)
        if OrganizationPolicy.model_validate(space.organization).model is None or space.authority is None:
            raise conflict(MemorySpaceRow.KIND, space.id, "organization_not_configured")
        state = await session.get(SpaceOrganizationRow, space_id)
        if state is None:
            state = SpaceOrganizationRow(space_id=space_id)
            session.add(state)
        if state.active_run_id is None:
            state.pending, state.due_at = True, None
        await session.flush()
        audit_row(session, actor, space, "organize")
        return status_view(state)


async def history(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    space_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> RunPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_row(session, actor, MemorySpaceRow, scope, space_id, "read")
        state = await session.get(SpaceOrganizationRow, space_id)
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(RunRow).where(RunRow.session_id == (state.session_id if state is not None else "")),
            (RunRow.created_at, RunRow.id),
            kind="space_organizations",
            owner=space_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return RunPage(items=await run_views(session, rows), next_cursor=next_cursor)


async def associations(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    space_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> SessionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_row(session, actor, MemorySpaceRow, scope, space_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(SessionRow).where(
                SessionRow.workspace_id == scope.workspace_id,
                SessionRow.memory.contains({"spaces": [{"space_id": space_id}]}),
            ),
            SessionRow.id,
            kind="space_sessions",
            owner=space_id,
            cursor=cursor,
            limit=limit,
        )
        return SessionPage(items=await session_views(session, rows), next_cursor=next_cursor)


async def delete_space(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    space_id: str,
    *,
    if_match: str | None,
) -> None:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        space = await find_row(session, actor, MemorySpaceRow, scope, space_id, "write", lock=True)
        require_match(if_match, space.id, space.version)
        bound = await session.scalar(
            select(SessionRow.id)
            .where(
                SessionRow.workspace_id == scope.workspace_id,
                SessionRow.memory.contains({"spaces": [{"space_id": space_id}]}),
            )
            .limit(1)
        )
        if bound is not None:
            raise conflict(MemorySpaceRow.KIND, space_id, "space_bound", session_id=bound)
        active = await session.scalar(
            select(RunRow.id)
            .where(
                RunRow.workspace_id == scope.workspace_id,
                RunRow.sealed_at.is_(None),
                RunRow.memory_mounts.contains([{"space_id": space_id}]),
            )
            .limit(1)
        )
        if active is not None:
            raise conflict(MemorySpaceRow.KIND, space_id, "space_in_use", run_id=active)
        memory = await session.get_one(MemoryRow, space.memory_id)
        audit_row(session, actor, space, "delete")
        await session.delete(space)
        await session.flush()
        await session.delete(memory)
