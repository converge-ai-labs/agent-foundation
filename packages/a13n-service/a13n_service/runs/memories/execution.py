"""A run's memories: the memories its frozen mounts still name, in the root agent's file and record memory
capabilities.

The plan session reads each mounted memory, skipping memories deleted since the run froze its mounts and record
memories whose provider is disabled. A file memory gets one PostgreSQL store; a record memory gets one store of its
provider, opened for the attempt over the host transport and skipped when it does not open. Each store call first
rechecks the run principal under the run's frozen authority: a refusal is the store error `forbidden`, and a
memory deleted meanwhile is `memory_deleted`. A record store also rechecks that its provider is still enabled
(`unavailable`), in its own short session before the backend call. The file capability shares the attempt's
cursors, which each checkpoint commits.
"""

from collections.abc import AsyncIterator, Callable, Collection
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from a13n_harness.capabilities import (
    FileMemoryCapability,
    FileMemoryLimits,
    FileMount,
    FileToolKey,
    MemoryCursors,
    RecordMemoryCapability,
    RecordMemoryLimits,
    RecordMount,
    RecordToolKey,
)
from a13n_harness.providers.memory import MemoryRecord, MemoryStoreError, Origin, RecordPage, RecordStore
from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.memories.records import RecordMemory, backend, open_record_store, record_memory
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.memories.service import inherited_guide
from a13n_service.resources.memories.store import PostgresFileStore, file_format
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.providers.tables import MemoryProviderRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import RunInput
from a13n_service.runs.tables import RunRow
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize

logger = get_logger(__name__)

type Gate = Callable[[Verb], None]


@dataclass(frozen=True, slots=True)
class PlannedMemory:
    """One frozen mount whose memory exists, with the memory's configuration as the attempt read it."""

    mount: MemoryMount
    guide: str | None
    always_load: tuple[str, ...]
    # A record memory with its enabled provider; None for a file memory.
    record: RecordMemory | None = None


async def resolve_memories(session: AsyncSession, run: RunRow) -> tuple[PlannedMemory, ...]:
    mounts = [MemoryMount.model_validate(mount) for mount in run.memory_mounts]
    if not mounts:
        return ()
    rows = {
        row.id: row
        for row in await session.scalars(
            select(MemoryRow).where(
                MemoryRow.workspace_id == run.workspace_id,
                MemoryRow.id.in_([mount.memory_id for mount in mounts]),
            )
        )
    }
    planned: list[PlannedMemory] = []
    for mount in mounts:
        if (row := rows.get(mount.memory_id)) is None:
            continue
        record = None
        if row.kind == "record":
            try:
                record = await record_memory(session, row)
            except ServiceError as error:
                if error.code != "disabled":
                    raise
                logger.warning("Record memory skipped", extra={"memory_id": row.id, "reason": "provider_disabled"})
                continue
        planned.append(PlannedMemory(mount, row.guide, tuple(row.always_load), record))
    return tuple(planned)


def _gate(run: RunInput, principal: Principal, authority: ExecutionAuthority) -> Gate:
    """The per-call check of the run principal under the run's frozen authority."""
    scope = WorkspaceScope(run.organization_id, run.workspace_id)

    def gate(verb: Verb) -> None:
        try:
            authorize(principal, scope, verb, authority=authority)
        except ServiceError as error:
            if error.code != "forbidden":
                raise
            raise MemoryStoreError("forbidden", "The run is not allowed to use this memory") from None

    return gate


def _guide(memory: PlannedMemory, settings: MemorySettings) -> str:
    if memory.guide is not None:
        return memory.guide
    return inherited_guide(settings, "file" if memory.record is None else "record")


def file_memory(
    storage: Storage,
    settings: MemorySettings,
    planned: tuple[PlannedMemory, ...],
    *,
    run: RunInput,
    principal: Principal,
    authority: ExecutionAuthority,
    cursors: MemoryCursors,
    tools: Collection[FileToolKey],
) -> FileMemoryCapability | None:
    """The capability over the planned file memories, or None when the run mounts none."""
    files = [memory for memory in planned if memory.record is None]
    if not files:
        return None
    gate = _gate(run, principal, authority)
    mounts = [
        FileMount(
            name=memory.mount.name,
            store=PostgresFileStore(storage, memory.mount.memory_id, settings, gate=gate),
            access=memory.mount.access,
            guide=_guide(memory, settings),
            always_load=memory.always_load,
            cursor_key=memory.mount.memory_id,
        )
        for memory in files
    ]
    limits = FileMemoryLimits(
        format=file_format(settings),
        context_bytes=settings.context_bytes,
        always_load_bytes=settings.always_load_bytes,
        write_retries=settings.write_retries,
    )
    origin = Origin(run_id=run.id, principal_id=run.principal_id)
    return FileMemoryCapability(mounts, limits=limits, cursors=cursors, origin=origin, tools=tools)


@dataclass(frozen=True, slots=True)
class RunRecordStore:
    """A record memory's store as a run uses it: every call rechecks the run and the memory, then calls the backend
    outside any session."""

    store: RecordStore
    storage: Storage
    memory: RecordMemory
    gate: Gate

    async def _check(self, verb: Verb) -> None:
        self.gate(verb)
        async with short_session(self.storage) as session:
            enabled = await session.scalar(
                select(MemoryProviderRow.enabled)
                .join(MemoryRow, MemoryRow.provider_id == MemoryProviderRow.id)
                .where(MemoryRow.id == self.memory.id)
            )
        if enabled is None:
            raise MemoryStoreError("memory_deleted", "The memory was deleted")
        if not enabled:
            raise MemoryStoreError("unavailable", "The memory's provider is disabled")

    async def search(self, query: str, *, limit: int) -> tuple[MemoryRecord, ...]:
        await self._check("read")
        with backend():
            return await self.store.search(query, limit=limit)

    async def list(self, *, limit: int, cursor: str | None = None) -> RecordPage:
        await self._check("read")
        with backend():
            return await self.store.list(limit=limit, cursor=cursor)

    async def add(self, text: str) -> MemoryRecord:
        await self._check("run")
        with backend(write=True):
            return await self.store.add(text)

    async def update(self, record_id: str, text: str) -> MemoryRecord:
        await self._check("run")
        with backend(write=True):
            return await self.store.update(record_id, text)

    async def delete(self, record_id: str) -> None:
        await self._check("run")
        with backend(write=True):
            await self.store.delete(record_id)

    async def purge(self) -> None:
        await self._check("write")
        with backend():
            await self.store.purge()


@asynccontextmanager
async def record_memory_capability(
    runtime: Runtime,
    planned: tuple[PlannedMemory, ...],
    *,
    run: RunInput,
    principal: Principal,
    authority: ExecutionAuthority,
    tools: Collection[RecordToolKey],
) -> AsyncIterator[RecordMemoryCapability | None]:
    """The capability over the planned record memories whose stores open, which stay open until the context
    exits; None when there is none."""
    gate = _gate(run, principal, authority)
    settings = runtime.settings.memory
    async with AsyncExitStack() as stack:
        mounts: list[RecordMount] = []
        for memory in planned:
            if (record := memory.record) is None:
                continue
            try:
                store = await stack.enter_async_context(open_record_store(runtime, record.provider, record.namespace))
            except (ServiceError, ValueError, MemoryStoreError) as error:
                logger.warning(
                    "Record memory skipped",
                    extra={"memory_id": record.id, "reason": "store_unavailable", "error_type": type(error).__name__},
                )
                continue
            mounts.append(
                RecordMount(
                    name=memory.mount.name,
                    store=RunRecordStore(store, runtime.storage, record, gate),
                    access=memory.mount.access,
                    guide=_guide(memory, settings),
                    recall=memory.mount.recall,
                )
            )
        limits = RecordMemoryLimits(
            record_chars=settings.record_chars,
            recall_limit=settings.recall_limit,
            recall_bytes=settings.recall_bytes,
            recall_seconds=settings.recall_seconds,
        )
        yield RecordMemoryCapability(mounts, limits=limits, tools=tools) if mounts else None
