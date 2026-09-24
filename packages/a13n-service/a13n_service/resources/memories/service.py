"""Memories as workspace resources: create, configure and delete them, and resolve one for a mount.

A memory is live, without revisions. Its guide and always-loaded paths shape every thread that mounts it, so
changing them needs `write`, as creating and deleting do. Deleting a memory deletes its files, history and
thread mounts in the same transaction; a run that still holds it finds it deleted at its next memory call.
"""

from collections.abc import Sequence

from a13n_harness.capabilities import DEFAULT_FILE_GUIDE
from a13n_harness.providers.memory import MemoryStoreError, validate_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, assign, short_session, transaction, unique_key
from a13n_service.infra.errors import invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.resources.memories.schemas import Memory, MemoryCreate, MemoryPage, MemoryUpdate
from a13n_service.resources.memories.store import file_format
from a13n_service.resources.memories.tables import MemoryFileStoreRow, MemoryRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize


def effective_guide(guide: str | None, settings: MemorySettings) -> str:
    """The memory's own guide, else the deployment's default, else the one built into the Harness."""
    if guide is not None:
        return guide
    return DEFAULT_FILE_GUIDE if settings.default_guide.file is None else settings.default_guide.file


def memory_view(row: MemoryRow, store: MemoryFileStoreRow, settings: MemorySettings) -> Memory:
    return Memory(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        key=row.key,
        name=row.name,
        description=row.description,
        kind="file",
        type="postgres",
        guide=row.guide,
        effective_guide=effective_guide(row.guide, settings),
        always_load=row.always_load,
        labels=row.labels,
        file_count=store.file_count,
        content_bytes=store.content_bytes,
        history_bytes=store.history_bytes,
        version=row.version,
        created_by_id=row.created_by_id,
        updated_by_id=row.updated_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _views(session: AsyncSession, rows: Sequence[MemoryRow], settings: MemorySettings) -> list[Memory]:
    stores = {
        store.memory_id: store
        for store in await session.scalars(
            select(MemoryFileStoreRow)
            .where(MemoryFileStoreRow.memory_id.in_([row.id for row in rows]))
            .execution_options(populate_existing=True)
        )
    }
    return [memory_view(row, stores[row.id], settings) for row in rows]


def _guide(guide: str | None, settings: MemorySettings) -> str | None:
    if guide is not None and len(guide.encode()) > settings.guide_bytes:
        raise invalid("guide", f"exceeds {settings.guide_bytes} bytes")
    return guide


def _always_load(paths: Sequence[str], settings: MemorySettings) -> list[str]:
    fmt = file_format(settings)
    normalized: list[str] = []
    for index, path in enumerate(paths):
        try:
            normalized.append(validate_path(path, fmt))
        except MemoryStoreError as error:
            raise invalid(f"always_load.{index}", str(error)) from None
    if len(set(normalized)) != len(normalized):
        raise invalid("always_load", "paths must be unique")
    return normalized


async def create_memory(
    storage: Storage, actor: Principal, workspace_id: str, body: MemoryCreate, *, settings: MemorySettings
) -> Memory:
    with unique_key(MemoryRow.KIND, "uq_memories_workspace_id_key", body.key):
        async with transaction(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "write")
            row = MemoryRow(
                id=new_object_id("mem"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                key=body.key,
                name=body.name,
                description=body.description,
                kind="file",
                type=body.type,
                guide=_guide(body.guide, settings),
                always_load=_always_load(body.always_load, settings),
                labels=body.labels,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(row)
            await session.flush()
            store = MemoryFileStoreRow(
                memory_id=row.id, seq=0, pruned_through_seq=0, content_bytes=0, history_bytes=0, file_count=0
            )
            session.add(store)
            await session.flush()
            audit_row(session, actor, row, "create")
            return memory_view(row, store, settings)


async def list_memories(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    labels: list[str],
    limit: int,
    cursor: str | None,
    settings: MemorySettings,
) -> MemoryPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(MemoryRow).where(
                MemoryRow.workspace_id == scope.workspace_id, label_filter(MemoryRow.labels, labels)
            ),
            MemoryRow.id,
            kind="memories",
            owner=cursors.query_owner(scope.workspace_id, labels),
            cursor=cursor,
            limit=limit,
        )
        return MemoryPage(items=await _views(session, rows, settings), next_cursor=next_cursor)


async def get_memory(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, *, settings: MemorySettings
) -> Memory:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, MemoryRow, scope, memory_id, "read")
        [view] = await _views(session, [row], settings)
        return view


async def update_memory(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    body: MemoryUpdate,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> Memory:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, MemoryRow, scope, memory_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name", "labels")
        if "description" in body.model_fields_set:
            values["description"] = body.description
        if "guide" in body.model_fields_set:
            values["guide"] = _guide(body.guide, settings)
        if body.always_load is not None:
            values["always_load"] = _always_load(body.always_load, settings)
        if record_update(session, actor, row, assign(row, values)):
            await session.flush()
        [view] = await _views(session, [row], settings)
        return view


async def delete_memory(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, *, if_match: str | None
) -> None:
    """Delete the memory with its files, history and thread mounts; runs holding it see it deleted."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, MemoryRow, scope, memory_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        audit_row(session, actor, row, "delete")
        await session.delete(row)


async def resolve_memory(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    memory_id: str,
    *,
    verb: Verb = "read",
    authority: ExecutionAuthority | None = None,
) -> MemoryRow:
    """A memory of the workspace the actor may `verb`, read in the caller's session."""
    authorize(actor, scope, verb, authority=authority)
    return await find_row(session, actor, MemoryRow, scope, memory_id, "read")
