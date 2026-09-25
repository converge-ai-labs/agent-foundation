"""Memories as workspace resources: create, configure and delete them, and resolve one for a mount.

A memory is live, without revisions. A file memory (`postgres`) keeps its files in the Service; a record memory
keeps its records in a Memory Provider's backend, under a namespace that belongs to it alone. Its guide and
always-loaded paths shape every thread that mounts it, so changing them needs `write`, as creating and deleting
do. Deleting a memory deletes its thread mounts in the same transaction, with a file memory's files and history;
a record memory's namespace is purged through the outbox. A run that still holds it finds it deleted at its next
memory call.
"""

import hashlib
from collections.abc import Sequence

from a13n_harness.capabilities import DEFAULT_FILE_GUIDE, DEFAULT_RECORD_GUIDE
from a13n_harness.providers.memory import MemoryStoreError, validate_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, advisory_lock, assign, short_session, transaction, unique_key
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.infra.outbox import OutboxKind, OutboxRow, enqueue
from a13n_service.resources.memories.schemas import POSTGRES, Memory, MemoryCreate, MemoryPage, MemoryUpdate
from a13n_service.resources.memories.store import file_format
from a13n_service.resources.memories.tables import MemoryFileStoreRow, MemoryKind, MemoryRow
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import MemoryProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize

PURGE: OutboxKind = "memory_purge"


def inherited_guide(settings: MemorySettings, kind: MemoryKind) -> str:
    """The guide of a memory without its own: the deployment's default for its kind, else the Harness's."""
    if kind == "file":
        return DEFAULT_FILE_GUIDE if settings.default_guide.file is None else settings.default_guide.file
    return DEFAULT_RECORD_GUIDE if settings.default_guide.record is None else settings.default_guide.record


def require_kind(memory: MemoryRow, kind: MemoryKind) -> None:
    """File operations need a file memory, record operations a record memory."""
    if memory.kind != kind:
        raise conflict(MemoryRow.KIND, memory.id, "memory_kind", expected=kind)


def default_namespace(memory_id: str) -> str:
    """The namespace a new record memory owns unless its creator adopts one."""
    return "a13n-" + hashlib.sha256(memory_id.encode()).hexdigest()[:32]


def memory_view(row: MemoryRow, store: MemoryFileStoreRow | None, settings: MemorySettings) -> Memory:
    return Memory(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        key=row.key,
        name=row.name,
        description=row.description,
        kind=row.kind,
        type=row.type,
        provider_id=row.provider_id,
        namespace=row.namespace,
        guide=row.guide,
        inherited_guide=inherited_guide(settings, row.kind),
        always_load=row.always_load,
        labels=row.labels,
        file_count=None if store is None else store.file_count,
        content_bytes=None if store is None else store.content_bytes,
        history_bytes=None if store is None else store.history_bytes,
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
            .where(MemoryFileStoreRow.memory_id.in_([row.id for row in rows if row.kind == "file"]))
            .execution_options(populate_existing=True)
        )
    }
    return [memory_view(row, stores.get(row.id), settings) for row in rows]


def _guide(guide: str | None, settings: MemorySettings) -> str | None:
    if guide is not None and len(guide.encode()) > settings.guide_bytes:
        raise invalid("guide", f"exceeds {settings.guide_bytes} bytes")
    return guide


def _always_load(kind: MemoryKind, paths: Sequence[str], settings: MemorySettings) -> list[str]:
    if kind != "file" and paths:
        raise invalid("always_load", "only file memories load files")
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


async def _lock_namespace(session: AsyncSession, provider_id: str, namespace: str) -> None:
    """Serialize a namespace's claims and purge staging until the transaction ends."""
    await advisory_lock(session, "memory-namespace", provider_id, namespace)


async def _claim_namespace(session: AsyncSession, provider_id: str, namespace: str) -> None:
    """Take a free namespace: no memory owns it, and no deleted memory's purge of it is still pending."""
    await _lock_namespace(session, provider_id, namespace)
    owner = await session.scalar(
        select(MemoryRow.id).where(MemoryRow.provider_id == provider_id, MemoryRow.namespace == namespace)
    )
    if owner is not None:
        raise ServiceError(
            "already_exists", f"A memory already owns namespace {namespace}", {"kind": "memory", "key": namespace}
        )
    purging = await session.scalar(
        select(OutboxRow.id)
        .where(
            OutboxRow.kind == PURGE,
            OutboxRow.status == "pending",
            OutboxRow.target["provider_id"].astext == provider_id,
            OutboxRow.target["namespace"].astext == namespace,
        )
        .limit(1)
    )
    if purging is not None:
        raise conflict(MemoryProviderRow.KIND, provider_id, "namespace_purging", namespace=namespace)


async def _backend(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, memory_id: str, body: MemoryCreate
) -> tuple[MemoryKind, str | None, str | None]:
    """The new memory's kind, provider and namespace: the Service's own store, or a claimed provider namespace."""
    if body.type == POSTGRES:
        for field in ("provider_id", "namespace"):
            if getattr(body, field) is not None:
                raise invalid(field, "the Service stores postgres memories itself")
        return "file", None, None
    if body.provider_id is None:
        raise invalid("provider_id", f"a {body.type} memory needs a memory provider")
    provider = await resolve_provider(session, actor, MemoryProviderRow, scope, body.provider_id)
    if provider.type != body.type:
        raise invalid("type", f"the memory provider is of type {provider.type}")
    namespace = body.namespace or default_namespace(memory_id)
    await _claim_namespace(session, provider.id, namespace)
    return "record", provider.id, namespace


async def create_memory(
    storage: Storage, actor: Principal, workspace_id: str, body: MemoryCreate, *, settings: MemorySettings
) -> Memory:
    """A file memory with its empty store, or a record memory owning a new or adopted provider namespace; the
    provider must be enabled and usable in the workspace."""
    with unique_key(MemoryRow.KIND, "uq_memories_workspace_id_key", body.key):
        async with transaction(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "write")
            memory_id = new_object_id("mem")
            kind, provider_id, namespace = await _backend(session, actor, scope, memory_id, body)
            row = MemoryRow(
                id=memory_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                key=body.key,
                name=body.name,
                description=body.description,
                kind=kind,
                type=body.type,
                provider_id=provider_id,
                namespace=namespace,
                guide=_guide(body.guide, settings),
                always_load=_always_load(kind, body.always_load, settings),
                labels=body.labels,
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(row)
            await session.flush()
            store = None
            if kind == "file":
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
    kind: MemoryKind | None,
    type_: str | None,
    limit: int,
    cursor: str | None,
    settings: MemorySettings,
) -> MemoryPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(MemoryRow).where(
            MemoryRow.workspace_id == scope.workspace_id, label_filter(MemoryRow.labels, labels)
        )
        if kind is not None:
            query = query.where(MemoryRow.kind == kind)
        if type_ is not None:
            query = query.where(MemoryRow.type == type_)
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            MemoryRow.id,
            kind="memories",
            owner=cursors.query_owner(scope.workspace_id, labels, kind, type_),
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
            values["always_load"] = _always_load(row.kind, body.always_load, settings)
        if record_update(session, actor, row, assign(row, values)):
            await session.flush()
        [view] = await _views(session, [row], settings)
        return view


async def delete_memory(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, *, if_match: str | None
) -> None:
    """Delete the memory with its thread mounts, and a file memory's files and history. A record memory's
    namespace, adopted or not, is purged by the `memory_purge` delivery staged here. Runs holding the memory see
    it deleted."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, MemoryRow, scope, memory_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row.provider_id is not None and row.namespace is not None:
            await _lock_namespace(session, row.provider_id, row.namespace)
            enqueue(
                session,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                kind=PURGE,
                dedupe_key=row.id,
                target={"provider_id": row.provider_id, "namespace": row.namespace},
                payload={"memory_id": row.id},
            )
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
