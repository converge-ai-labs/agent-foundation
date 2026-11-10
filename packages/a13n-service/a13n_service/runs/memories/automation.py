"""Standalone policy configuration and the shared history-consumption projection."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.resources.memories.schemas import OrganizationPolicy
from a13n_service.resources.memories.spaces import _authority
from a13n_service.resources.memories.tables import MemoryRow, MemorySpaceRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.memories import organization_history
from a13n_service.runs.memories.organization_schemas import MemoryAutomation
from a13n_service.runs.memories.organization_tables import FileOrganizationRow, OrganizationProgressRow
from a13n_service.runs.memories.spaces import status_view
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope


async def _memory(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, identifier: str, *, lock: bool = False
) -> MemoryRow:
    memory = await find_row(session, actor, MemoryRow, scope, identifier, "write" if lock else "read", lock=lock)
    if memory.kind != "file":
        raise conflict(MemoryRow.KIND, identifier, "file_memory_required")
    managed = await session.scalar(select(MemorySpaceRow.id).where(MemorySpaceRow.memory_id == identifier))
    if managed is not None:
        raise conflict(MemoryRow.KIND, identifier, "managed_memory", space_id=managed)
    return memory


async def consumed(session: AsyncSession, memory_id: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(OrganizationProgressRow)
            .where(OrganizationProgressRow.memory_id == memory_id)
        )
        or 0
    )


async def _view(session: AsyncSession, identifier: str, row: FileOrganizationRow | None) -> MemoryAutomation:
    return MemoryAutomation(
        id=identifier,
        policy=OrganizationPolicy() if row is None else OrganizationPolicy.model_validate(row.policy),
        status=status_view(row),
        processed_history_runs=await consumed(session, identifier),
        version=0 if row is None else row.version,
    )


async def get(runtime: Runtime, actor: Principal, workspace_id: str, identifier: str) -> MemoryAutomation:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await _memory(session, actor, scope, identifier)
        return await _view(session, identifier, await session.get(FileOrganizationRow, identifier))


async def update(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    identifier: str,
    policy: OrganizationPolicy,
    *,
    if_match: str | None,
) -> MemoryAutomation:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        memory = await _memory(session, actor, scope, identifier, lock=True)
        row = await session.get(FileOrganizationRow, identifier)
        require_match(if_match, identifier, 0 if row is None else row.version)
        authority = await _authority(session, actor, scope, policy)
        # Validate the Session allowlist even when it currently contains no sealed Runs.
        await organization_history.select_sources(session, actor, scope, policy.history, memory_id=identifier)
        value = policy.model_dump(mode="json", exclude_none=True)
        if row is None:
            row = FileOrganizationRow(memory_id=identifier, policy=value, authority=authority)
            session.add(row)
        elif row.policy != value or row.authority != authority:
            row.policy, row.authority = value, authority
            row.version += 1
            row.next_check_at = None
        else:
            return await _view(session, identifier, row)
        audit_row(session, actor, memory, "configure_organization")
        await session.flush()
        return await _view(session, identifier, row)
