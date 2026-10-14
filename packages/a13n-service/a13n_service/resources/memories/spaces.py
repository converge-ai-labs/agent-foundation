"""Memory Space configuration; execution and organization history belong to runs."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.resources.memories.schemas import (
    MemoryCreate,
    MemorySpace,
    MemorySpaceCreate,
    MemorySpacePage,
    MemorySpaceUpdate,
    OrganizationPolicy,
)
from a13n_service.resources.memories.service import _always_load, _guide, insert_memory
from a13n_service.resources.memories.tables import MemoryFileStoreRow, MemoryRow, MemorySpaceRow
from a13n_service.resources.models.service import resolve_model
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, execution_authority


async def _authority(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, policy: OrganizationPolicy
) -> dict | None:
    if policy.model is None:
        return None
    authority = execution_authority(actor, scope)
    await resolve_model(session, actor, scope, policy.model, verb="run", authority=authority)
    return authority.model_dump(mode="json")


async def views(session: AsyncSession, rows: Sequence[MemorySpaceRow]) -> list[MemorySpace]:
    backing = {
        memory.id: (memory, store)
        for memory, store in (
            await session.execute(
                select(MemoryRow, MemoryFileStoreRow)
                .join(MemoryFileStoreRow, MemoryFileStoreRow.memory_id == MemoryRow.id)
                .where(MemoryRow.id.in_([row.memory_id for row in rows]))
            )
        ).tuples()
    }
    result = []
    for row in rows:
        memory, store = backing[row.memory_id]
        result.append(
            MemorySpace(
                id=row.id,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                name=row.name,
                description=row.description,
                labels=row.labels,
                enabled=row.enabled,
                organization=OrganizationPolicy.model_validate(row.organization),
                memory_id=row.memory_id,
                guide=memory.guide,
                always_load=memory.always_load,
                file_count=store.file_count,
                content_bytes=store.content_bytes,
                history_bytes=store.history_bytes,
                version=row.version,
                created_by_id=row.created_by_id,
                updated_by_id=row.updated_by_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return result


async def create_space(
    storage: Storage, actor: Principal, workspace_id: str, body: MemorySpaceCreate, *, settings: MemorySettings
) -> MemorySpace:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        authority = await _authority(session, actor, scope, body.organization)
        memory = await insert_memory(
            session,
            actor,
            scope,
            MemoryCreate(
                name=body.name,
                guide=body.guide,
                always_load=body.always_load,
            ),
            settings=settings,
        )
        row = MemorySpaceRow(
            id=new_object_id("mspace"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            memory_id=memory.id,
            name=body.name,
            description=body.description,
            labels=body.labels,
            enabled=body.enabled,
            organization=body.organization.model_dump(mode="json"),
            authority=authority,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        await session.flush()
        audit_row(session, actor, row, "create")
        return (await views(session, [row]))[0]


async def list_spaces(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    labels: list[str],
    enabled: bool | None,
    limit: int,
    cursor: str | None,
) -> MemorySpacePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(MemorySpaceRow).where(
            MemorySpaceRow.workspace_id == scope.workspace_id, label_filter(MemorySpaceRow.labels, labels)
        )
        if enabled is not None:
            query = query.where(MemorySpaceRow.enabled == enabled)
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            MemorySpaceRow.id,
            kind="memory_spaces",
            owner=cursors.query_owner(scope.workspace_id, labels, enabled),
            cursor=cursor,
            limit=limit,
        )
        return MemorySpacePage(items=await views(session, rows), next_cursor=next_cursor)


async def get_space(storage: Storage, actor: Principal, workspace_id: str, space_id: str) -> MemorySpace:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, MemorySpaceRow, scope, space_id, "read")
        return (await views(session, [row]))[0]


async def update_space(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    space_id: str,
    body: MemorySpaceUpdate,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> MemorySpace:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, MemorySpaceRow, scope, space_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name", "labels", "enabled")
        if "description" in body.model_fields_set:
            values["description"] = body.description
        if body.organization is not None:
            values["organization"] = body.organization.model_dump(mode="json")
            values["authority"] = await _authority(session, actor, scope, body.organization)
        memory = await session.get_one(MemoryRow, row.memory_id)
        memory_values: dict[str, object] = {}
        if "guide" in body.model_fields_set:
            memory_values["guide"] = _guide(body.guide, settings)
        if body.always_load is not None:
            memory_values["always_load"] = _always_load("file", body.always_load, settings)
        changed = assign(row, values) + assign(memory, memory_values)
        if record_update(session, actor, row, changed):
            # A backing-only edit must also advance the owning Space's ETag.
            if memory_values:
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(row, "organization")
            await session.flush()
        return (await views(session, [row]))[0]
