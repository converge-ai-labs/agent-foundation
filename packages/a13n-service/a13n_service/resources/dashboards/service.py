"""Workspace document editing, independent of authority to query monitoring targets."""

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, advisory_lock, assign, short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.dashboards.schemas import (
    Dashboard,
    DashboardConfig,
    DashboardCreate,
    DashboardPage,
    DashboardUpdate,
    default_config,
)
from a13n_service.resources.dashboards.tables import DashboardRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def list_dashboards(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> DashboardPage:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await session.execute(
            insert(DashboardRow)
            .values(
                id=new_object_id("dsh"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                name="Environment overview",
                config=default_config().model_dump(mode="json"),
                builtin=True,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            .on_conflict_do_nothing(index_elements=[DashboardRow.workspace_id], index_where=text("builtin"))
        )
        rows, next_cursor = await cursors.id_page(
            session,
            select(DashboardRow).where(DashboardRow.workspace_id == scope.workspace_id),
            DashboardRow.id,
            kind="metrics_dashboards",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return DashboardPage(items=[Dashboard.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_preset(storage: Storage, actor: Principal, workspace_id: str) -> DashboardConfig:
    async with short_session(storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
    return default_config()


async def get_dashboard(storage: Storage, actor: Principal, workspace_id: str, dashboard_id: str) -> Dashboard:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return Dashboard.model_validate(await find_row(session, actor, DashboardRow, scope, dashboard_id, "read"))


async def create_dashboard(storage: Storage, actor: Principal, workspace_id: str, body: DashboardCreate) -> Dashboard:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        await advisory_lock(session, "metrics_dashboards", scope.workspace_id)
        count = await session.scalar(
            select(func.count())
            .select_from(DashboardRow)
            .where(
                DashboardRow.workspace_id == scope.workspace_id,
                DashboardRow.builtin.is_(False),
            )
        )
        if (count or 0) >= 64:
            raise conflict("workspace", scope.workspace_id, "dashboard_limit", limit=64)
        row = DashboardRow(
            id=new_object_id("dsh"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name,
            config=body.config.model_dump(mode="json"),
            builtin=False,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        audit_row(session, actor, row, "create")
        await session.flush()
        return Dashboard.model_validate(row)


async def update_dashboard(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    dashboard_id: str,
    body: DashboardUpdate,
    *,
    if_match: str | None,
) -> Dashboard:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, DashboardRow, scope, dashboard_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name")
        if body.config is not None:
            values["config"] = body.config.model_dump(mode="json")
        if record_update(session, actor, row, assign(row, values)):
            await session.flush()
        return Dashboard.model_validate(row)


async def delete_dashboard(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    dashboard_id: str,
    *,
    if_match: str | None,
) -> None:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, DashboardRow, scope, dashboard_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row.builtin:
            raise conflict(row.KIND, row.id, "builtin_dashboard")
        audit_row(session, actor, row, "delete")
        await session.delete(row)
