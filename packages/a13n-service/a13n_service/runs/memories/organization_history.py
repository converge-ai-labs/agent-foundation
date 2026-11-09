"""Frozen source Runs and their read boundary for memory organization."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import invalid
from a13n_service.runs.memories.organization_schemas import OrganizationHistory, OrganizationSource
from a13n_service.runs.memories.organization_tables import OrganizationProgressRow, OrganizationRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.sessions import find_session
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

TOOLS = frozenset({"list_organization_sources", "get_run", "list_run_items", "read_content", "search_content"})


async def select_sources(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    selection: OrganizationHistory | None,
    *,
    memory_id: str | None = None,
) -> list[OrganizationSource]:
    if selection is None:
        return []
    for identifier in selection.session_ids:
        owner = await find_session(session, scope.workspace_id, identifier)
        if owner.purpose != "conversation":
            raise invalid("history.session_ids", "Select conversation Sessions")
    query = (
        select(RunRow)
        .join(ThreadRow, ThreadRow.id == RunRow.thread_id)
        .where(
            RunRow.workspace_id == scope.workspace_id,
            RunRow.session_id.in_(selection.session_ids),
            RunRow.sealed_at.is_not(None),
            ThreadRow.purpose == "conversation",
        )
    )
    if memory_id is not None:
        query = query.where(
            ~exists().where(
                OrganizationProgressRow.memory_id == memory_id,
                OrganizationProgressRow.source_run_id == RunRow.id,
            )
        )
    if selection.after is not None:
        query = query.where(RunRow.sealed_at >= selection.after)
    if selection.before is not None:
        query = query.where(RunRow.sealed_at <= selection.before)
    rows = list(
        await session.scalars(
            query.order_by(RunRow.sealed_at, RunRow.id).limit(selection.max_runs + (memory_id is None))
        )
    )
    if memory_id is None and len(rows) > selection.max_runs:
        raise invalid(
            "history.max_runs", "History exceeds max_runs; narrow the seal-time window or select fewer Sessions"
        )
    return [
        OrganizationSource(session_id=row.session_id, thread_id=row.thread_id, run_id=row.id, sealed_at=row.sealed_at)
        for row in rows
        if row.sealed_at is not None
    ]


async def sources(
    runtime: Runtime, actor: Principal, workspace_id: str, origin: str | None
) -> list[OrganizationSource] | None:
    """None means an ordinary caller; an empty list is an organization with no history authority."""
    if origin is None:
        return None
    async with short_session(runtime.storage) as session:
        row = await session.scalar(
            select(OrganizationRow).where(
                OrganizationRow.workspace_id == workspace_id, OrganizationRow.run_id == origin
            )
        )
        if row is None:
            return None
        await workspace_scope(session, actor, workspace_id, "read")
        return [OrganizationSource.model_validate(item) for item in row.sources]


async def authorize_tool(
    runtime: Runtime, actor: Principal, workspace_id: str, origin: str | None, name: str, arguments: dict
) -> None:
    selected = await sources(runtime, actor, workspace_id, origin)
    if selected is None:
        return
    if name not in TOOLS:
        raise invalid("tool", "An organization reads only its selected source Runs")
    if name in {"get_run", "list_run_items"} and not any(item.run_id == arguments.get("run_id") for item in selected):
        raise invalid("run_id", "Outside the organization history selection")


async def authorize_content(
    runtime: Runtime, actor: Principal, workspace_id: str, origin: str | None, run_id: str | None
) -> None:
    selected = await sources(runtime, actor, workspace_id, origin)
    if selected is not None and not any(item.run_id == run_id for item in selected):
        raise invalid("reference", "Outside the organization history selection")
