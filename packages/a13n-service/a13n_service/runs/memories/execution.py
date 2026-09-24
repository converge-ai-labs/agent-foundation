"""A run's file memories: the memories its frozen mounts still name, one store each, in the root agent's file
memory capability.

The plan session reads each mounted memory's guide and always-loaded paths, skipping memories deleted since the
run froze its mounts. Each store call first rechecks the run principal under the run's frozen authority: a
refusal is the store error `forbidden`, and a memory deleted meanwhile is `memory_deleted`. The capability
shares the attempt's cursors, which each checkpoint commits.
"""

from collections.abc import Collection
from dataclasses import dataclass

from a13n_harness.capabilities import FileMemoryCapability, FileMemoryLimits, FileMount, FileToolKey, MemoryCursors
from a13n_harness.providers.memory import MemoryStoreError, Origin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.memories.service import effective_guide
from a13n_service.resources.memories.store import PostgresFileStore, file_format
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.runs.tables import RunRow
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize


@dataclass(frozen=True, slots=True)
class PlannedMemory:
    """One frozen mount whose memory exists, with the memory's configuration as the attempt read it."""

    mount: MemoryMount
    guide: str | None
    always_load: tuple[str, ...]


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
    return tuple(
        PlannedMemory(mount, row.guide, tuple(row.always_load))
        for mount in mounts
        if (row := rows.get(mount.memory_id)) is not None
    )


def file_memory(
    storage: Storage,
    settings: MemorySettings,
    planned: tuple[PlannedMemory, ...],
    *,
    run: RunRow,
    principal: Principal,
    authority: ExecutionAuthority,
    cursors: MemoryCursors,
    tools: Collection[FileToolKey],
) -> FileMemoryCapability | None:
    """The capability over the planned memories, or None when the run mounts none."""
    if not planned:
        return None
    scope = WorkspaceScope(run.organization_id, run.workspace_id)

    def gate(verb: Verb) -> None:
        try:
            authorize(principal, scope, verb, authority=authority)
        except ServiceError as error:
            if error.code != "forbidden":
                raise
            raise MemoryStoreError("forbidden", "The run is not allowed to use this memory") from None

    mounts = [
        FileMount(
            name=memory.mount.name,
            store=PostgresFileStore(storage, memory.mount.memory_id, settings, gate=gate),
            access=memory.mount.access,
            guide=effective_guide(memory.guide, settings),
            always_load=memory.always_load,
            cursor_key=memory.mount.memory_id,
        )
        for memory in planned
    ]
    limits = FileMemoryLimits(
        format=file_format(settings),
        context_bytes=settings.context_bytes,
        always_load_bytes=settings.always_load_bytes,
        write_retries=settings.write_retries,
    )
    origin = Origin(run_id=run.id, principal_id=run.principal_id)
    return FileMemoryCapability(mounts, limits=limits, cursors=cursors, origin=origin, tools=tools)
