"""Thread memory mounts, the agent's default mounts, and the mount set a run freezes at acceptance.

A thread mounts memories of its workspace by name, at most `memory.mounts_per_thread`. At a thread's first
acceptance its agent's default mounts join, each whose name and memory the thread does not use yet; afterwards
only the thread's own mounts count. A fork copies its origin thread's mounts, and a child thread adopts the
mounts its parent run froze. Mount edits are thread operations under the thread `If-Match` and affect later
runs only.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.errors import ServiceError, at_field, conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.resources.memories.schemas import MemoryMount, SessionMemorySettings
from a13n_service.resources.memories.tables import MemoryRow, MemorySpaceRow
from a13n_service.resources.rows import given
from a13n_service.runs.memories.organization_tables import SpaceOrganizationRow
from a13n_service.runs.memories.schemas import FrozenMemoryMount, MemoryMountPage, MemoryMountUpdate
from a13n_service.runs.memories.tables import ThreadMemoryRow
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import get_thread, refresh_version, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope


async def thread_mounts(session: AsyncSession, thread_id: str) -> list[ThreadMemoryRow]:
    return list(
        (
            await session.scalars(
                select(ThreadMemoryRow).where(ThreadMemoryRow.thread_id == thread_id).order_by(ThreadMemoryRow.name)
            )
        ).all()
    )


async def _existing(session: AsyncSession, workspace_id: str, memory_ids: Sequence[str]) -> set[str]:
    """Which of the memories exist in the workspace, share-locked so none is deleted before the mounts commit."""
    rows = await session.scalars(
        select(MemoryRow.id)
        .where(MemoryRow.workspace_id == workspace_id, MemoryRow.id.in_(memory_ids))
        .order_by(MemoryRow.id)
        .with_for_update(read=True)
    )
    return set(rows)


def _row(thread: ThreadRow, mount: MemoryMount) -> ThreadMemoryRow:
    return ThreadMemoryRow(
        thread_id=thread.id,
        memory_id=mount.memory_id,
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        name=mount.name,
        access=mount.access,
        recall=mount.recall,
    )


async def mount_memories(
    session: AsyncSession, thread: ThreadRow, mounts: Sequence[MemoryMount], *, limit: int
) -> list[ThreadMemoryRow]:
    """Add mounts under the caller's thread lock: each name and memory new to the thread, each memory of the
    thread's workspace, at most `limit` in all."""
    if not mounts:
        return []
    taken = await thread_mounts(session, thread.id)
    if len(taken) + len(mounts) > limit:
        raise conflict("thread", thread.id, "memory_mount_limit", limit=limit)
    names, memory_ids = {mount.name for mount in taken}, {mount.memory_id for mount in taken}
    for mount in mounts:
        if mount.name in names:
            raise ServiceError(
                "already_exists",
                "The thread already mounts a memory by this name",
                {"kind": "mount", "key": mount.name},
            )
        if mount.memory_id in memory_ids:
            raise conflict(MemoryRow.KIND, mount.memory_id, "already_mounted")
        names.add(mount.name)
        memory_ids.add(mount.memory_id)
    existing = await _existing(session, thread.workspace_id, [mount.memory_id for mount in mounts])
    for mount in mounts:
        if mount.memory_id not in existing:
            raise not_found(MemoryRow.KIND, mount.memory_id)
    managed = await session.scalar(select(MemorySpaceRow).where(MemorySpaceRow.memory_id.in_(memory_ids)).limit(1))
    if managed is not None:
        raise conflict(MemoryRow.KIND, managed.memory_id, "managed_memory", space_id=managed.id)
    rows = [_row(thread, mount) for mount in mounts]
    session.add_all(rows)
    await session.flush()
    return rows


async def shared_memory_mounts(session: AsyncSession, origin: ThreadRow) -> list[MemoryMount]:
    """The origin thread's mounts, which a fork copies; the caller holds the origin thread lock."""
    return [MemoryMount.model_validate(mount) for mount in await thread_mounts(session, origin.id)]


async def adopt_memory_mounts(session: AsyncSession, child: ThreadRow, frozen: Sequence[dict]) -> None:
    """A child thread mounts the memories its parent run froze, except those deleted since."""
    mounts = [MemoryMount.model_validate(mount) for mount in frozen if mount.get("space_id") is None]
    existing = await _existing(session, child.workspace_id, [mount.memory_id for mount in mounts])
    session.add_all(_row(child, mount) for mount in mounts if mount.memory_id in existing)
    await session.flush()


async def freeze_memories(
    session: AsyncSession, thread: ThreadRow, defaults: Sequence[MemoryMount], *, limit: int
) -> list[dict]:
    """The mount set a new run uses. At the thread's first acceptance the agent's `defaults` join first; a
    default whose memory is gone refuses the run. The caller holds the thread lock."""
    if thread.purpose == "memory_organization":
        space = await session.scalar(
            select(MemorySpaceRow)
            .join(
                SpaceOrganizationRow,
                SpaceOrganizationRow.space_id == MemorySpaceRow.id,
            )
            .where(SpaceOrganizationRow.session_id == thread.session_id)
            .with_for_update(of=MemorySpaceRow, read=True)
        )
        if space is None or not space.enabled:
            return []
        return [
            FrozenMemoryMount(
                name="space",
                memory_id=space.memory_id,
                space_id=space.id,
                access="write",
                roots=("global/", "sessions/"),
            ).model_dump(mode="json")
        ]
    if thread.last_run_id is None and defaults:
        taken = await thread_mounts(session, thread.id)
        names, memory_ids = {mount.name for mount in taken}, {mount.memory_id for mount in taken}
        added = [mount for mount in defaults if mount.name not in names and mount.memory_id not in memory_ids]
        existing = await _existing(session, thread.workspace_id, [mount.memory_id for mount in added])
        for index, mount in enumerate(defaults):
            if mount in added and mount.memory_id not in existing:
                with at_field(f"memory_mounts.{index}.memory_id"):
                    raise not_found(MemoryRow.KIND, mount.memory_id)
        await mount_memories(session, thread, added, limit=limit)
    mounts = [
        mount.model_dump(mode="json")
        for mount in (MemoryMount.model_validate(row) for row in await thread_mounts(session, thread.id))
    ]
    owner = await session.get_one(SessionRow, thread.session_id)
    settings = SessionMemorySettings.model_validate(owner.memory)
    if not settings.enabled:
        return mounts
    if thread.origin == "child":
        parent = await session.get_one(RunRow, thread.origin_run_id)
        selected = [FrozenMemoryMount.model_validate(item) for item in parent.memory_mounts if item.get("space_id")]
        ids = [item.space_id for item in selected]
    else:
        selected = []
        ids = [item.space_id for item in settings.spaces]
    spaces = {
        row.id: row
        for row in await session.scalars(
            select(MemorySpaceRow)
            .where(MemorySpaceRow.workspace_id == thread.workspace_id, MemorySpaceRow.id.in_(ids))
            .order_by(MemorySpaceRow.id)
            .with_for_update(read=True)
        )
    }
    if thread.origin != "child":
        selected = [
            FrozenMemoryMount(
                name=binding.name,
                memory_id=spaces[binding.space_id].memory_id,
                space_id=binding.space_id,
                access=binding.access,
                roots=("global/", f"sessions/{owner.id}/"),
            )
            for binding in settings.spaces
            if binding.space_id in spaces
        ]
    names = {mount["name"] for mount in mounts}
    for mount in selected:
        if mount.space_id not in spaces or not spaces[mount.space_id].enabled:
            continue
        if mount.name in names:
            raise conflict("thread", thread.id, "memory_mount_name", name=mount.name)
        names.add(mount.name)
        mounts.append(mount.model_dump(mode="json"))
    if len(mounts) > limit:
        raise conflict("thread", thread.id, "memory_mount_limit", limit=limit)
    return sorted(mounts, key=lambda mount: mount["name"])


def inherited_cursors(parent: RunRow | None, mounts: Sequence[dict]) -> dict[str, str | None]:
    """The parent run's delivered cursors, for each memory still mounted under the same name: the history a run
    continues holds that memory's context as of the cursor. Any other memory gets its full context."""
    if parent is None:
        return {}
    before = {(item.name, item.cursor_key) for item in map(FrozenMemoryMount.model_validate, parent.memory_mounts)}
    return {
        mount.cursor_key: parent.memory_cursors[mount.cursor_key]
        for mount in map(FrozenMemoryMount.model_validate, mounts)
        if (mount.name, mount.cursor_key) in before and mount.cursor_key in parent.memory_cursors
    }


async def list_mounts(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str
) -> tuple[MemoryMountPage, int]:
    """The thread's memory mounts, and the thread version their edits must name."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        rows = await thread_mounts(session, thread.id)
        return MemoryMountPage(items=[MemoryMount.model_validate(row) for row in rows]), thread.version


def _audit(session: AsyncSession, actor: Principal, thread: ThreadRow, verb: str, name: str, memory_id: str) -> None:
    record(
        session,
        WorkspaceScope(thread.organization_id, thread.workspace_id),
        actor_id=actor.id,
        action=f"thread_memory.{verb}",
        target_kind="thread",
        target_id=thread.id,
        details={"name": name, "memory_id": memory_id},
    )


async def add_mount(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    body: MemoryMount,
    *,
    if_match: str | None,
    limit: int,
) -> tuple[MemoryMount, int]:
    """Mount a memory for the thread's later runs; returns the mount and the new thread version.

    Changing a mount's memory is explicit: remove the name, then add it again.
    """
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        require_open(thread)
        await mount_memories(session, thread, [body], limit=limit)
        _audit(session, actor, thread, "create", body.name, body.memory_id)
        await refresh_version(session, thread)
        return body, thread.version


async def update_mount(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    name: str,
    body: MemoryMountUpdate,
    *,
    if_match: str | None,
) -> tuple[MemoryMount, int]:
    """Change a mount's access or recall for the thread's later runs; returns the mount and the new thread
    version."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        require_open(thread)
        mount = await session.get(ThreadMemoryRow, (thread.id, name))
        if mount is None:
            raise not_found("mount", name)
        changed = assign(mount, given(body, "access", "recall"))
        if changed:
            _audit(session, actor, thread, "update", name, mount.memory_id)
            await refresh_version(session, thread)
        return MemoryMount.model_validate(mount), thread.version


async def remove_mount(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, name: str, *, if_match: str | None
) -> int:
    """Unmount for later runs; an active run keeps its frozen mount. Returns the new thread version."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        mount = await session.get(ThreadMemoryRow, (thread.id, name))
        if mount is None:
            raise not_found("mount", name)
        await session.delete(mount)
        _audit(session, actor, thread, "delete", name, mount.memory_id)
        await refresh_version(session, thread)
        return thread.version
