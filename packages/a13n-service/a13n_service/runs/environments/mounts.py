"""Desired thread mounts and the immutable mount set a run freezes at acceptance.

The frozen `runs.environment_mounts` of accepted and running runs is the durable active-use evidence that
stop and destroy check under the environment row lock; acceptance share-locks the same rows before installing it.
Desired mount edits are thread operations under the thread `If-Match`, and affect later acceptance only; a
new thread or fork takes its initial mounts through the same checks before its first acceptance.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.runs.environments.lifecycle import require_usable_state, reserve
from a13n_service.runs.environments.schemas import MAX_MOUNTS, MountCreate, MountPage, MountView
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import ThreadRow
from a13n_service.runs.threads import get_thread, refresh_version, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

# The primary sandbox's mount name: an agent with an environment template gets one reserved at acceptance.
PRIMARY = "workspace"


def has_primary(mounts: Sequence[Mapping[str, object]]) -> bool:
    """Whether a run's frozen mount set includes the primary sandbox."""
    return any(mount["name"] == PRIMARY for mount in mounts)


async def thread_has_primary(session: AsyncSession, thread: ThreadRow) -> bool:
    """Whether the thread's desired mounts include the primary sandbox."""
    return await session.get(ThreadEnvironmentRow, (thread.id, PRIMARY)) is not None


async def desired_mounts(session: AsyncSession, thread_id: str) -> list[ThreadEnvironmentRow]:
    return list(
        (
            await session.scalars(
                select(ThreadEnvironmentRow)
                .where(ThreadEnvironmentRow.thread_id == thread_id)
                .order_by(ThreadEnvironmentRow.name)
            )
        ).all()
    )


def _mount(thread: ThreadRow, environment_id: str, name: str, working_directory: str | None) -> ThreadEnvironmentRow:
    return ThreadEnvironmentRow(
        thread_id=thread.id,
        environment_id=environment_id,
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        name=name,
        working_directory=working_directory,
    )


async def shared_mounts(session: AsyncSession, origin: ThreadRow) -> list[MountCreate]:
    """The origin's desired mounts, which a fork shares unless it asks for fresh environments; the caller holds
    the origin thread lock."""
    return [
        MountCreate.model_validate(mount, from_attributes=True) for mount in await desired_mounts(session, origin.id)
    ]


async def adopt_mounts(session: AsyncSession, thread: ThreadRow, mounts: Sequence[EnvironmentMount]) -> None:
    """A child thread mounts what its parent run froze; the run that starts it locks and checks them."""
    for mount in mounts:
        session.add(_mount(thread, mount.environment_id, mount.name, mount.working_directory))
    await session.flush()


def require_usable(environment: EnvironmentRow, principal_id: str) -> None:
    """New use refuses retiring instances, unresolved permanent failures and other principals' devices."""
    if environment.owner_principal_id not in {None, principal_id}:
        raise ServiceError("forbidden", "Private environments are usable only by their owner", {"id": environment.id})
    if environment.status in {"deleting", "deleted"}:
        raise conflict("environment", environment.id, f"environment_{environment.status}")
    require_usable_state(environment)


async def lock_environments(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str], *, principal_id: str
) -> list[EnvironmentRow]:
    """Share-lock the workspace's instances in ID order, the order every multi-environment writer uses, and check
    each is usable. New use waits for a stop, delete or lifecycle step, which lock the row exclusively, but never
    for other new use."""
    ids = sorted(set(environment_ids))
    if not ids:
        return []
    rows = (
        await session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.workspace_id == workspace_id, EnvironmentRow.id.in_(ids))
            .order_by(EnvironmentRow.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {row.id for row in rows}
    for missing in ids:
        if missing not in found:
            raise not_found("environment", missing)
    for row in rows:
        require_usable(row, principal_id)
    return list(rows)


async def mount_environments(
    session: AsyncSession, thread: ThreadRow, mounts: Sequence[MountCreate], *, principal_id: str
) -> list[ThreadEnvironmentRow]:
    """Add desired mounts under the caller's thread lock: each name and instance new to the thread, at most
    `MAX_MOUNTS` in all, and each instance, locked in ID order, usable by the principal."""
    if not mounts:
        return []
    taken = await desired_mounts(session, thread.id)
    if len(taken) + len(mounts) > MAX_MOUNTS:
        raise conflict("thread", thread.id, "mount_limit", limit=MAX_MOUNTS)
    names, environment_ids = {mount.name for mount in taken}, {mount.environment_id for mount in taken}
    for mount in mounts:
        if mount.name in names:
            raise ServiceError(
                "already_exists", "The thread already has this mount", {"kind": "mount", "key": mount.name}
            )
        if mount.environment_id in environment_ids:
            raise conflict("environment", mount.environment_id, "already_mounted")
        names.add(mount.name)
        environment_ids.add(mount.environment_id)
    await lock_environments(
        session, thread.workspace_id, [mount.environment_id for mount in mounts], principal_id=principal_id
    )
    rows = [_mount(thread, mount.environment_id, mount.name, mount.working_directory) for mount in mounts]
    session.add_all(rows)
    await session.flush()
    return rows


async def reserve_primary(
    session: AsyncSession, principal: Principal, thread: ThreadRow, *, template_id: str, limit: int
) -> None:
    """Mount a new `creating` instance of the template as the thread's primary sandbox, unless the thread has one.

    The caller holds the thread lock. The instance is created later, from the template current at that time.
    """
    if await thread_has_primary(session, thread):
        return
    scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
    environment = await reserve(session, principal, scope, template_id, limit=limit)
    session.add(_mount(thread, environment.id, PRIMARY, None))
    await session.flush()


async def freeze_mounts(session: AsyncSession, thread: ThreadRow, *, principal_id: str) -> list[dict]:
    """The mount set a new run uses: the thread's desired mounts, each usable by the run's principal. The caller
    holds the thread lock; environments lock in ID order."""
    mounts = await desired_mounts(session, thread.id)
    await lock_environments(
        session, thread.workspace_id, [mount.environment_id for mount in mounts], principal_id=principal_id
    )
    return [
        EnvironmentMount(
            name=mount.name, environment_id=mount.environment_id, working_directory=mount.working_directory
        ).model_dump(mode="json")
        for mount in mounts
    ]


async def list_mounts(storage: Storage, actor: Principal, workspace_id: str, thread_id: str) -> tuple[MountPage, int]:
    """The thread's desired mounts, and the thread version their edits must name."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        rows = await desired_mounts(session, thread.id)
        return MountPage(items=[MountView.model_validate(row) for row in rows]), thread.version


def _audit(
    session: AsyncSession, actor: Principal, thread: ThreadRow, verb: str, name: str, environment_id: str
) -> None:
    record(
        session,
        WorkspaceScope(thread.organization_id, thread.workspace_id),
        actor_id=actor.id,
        action=f"thread_environment.{verb}",
        target_kind="thread",
        target_id=thread.id,
        details={"name": name, "environment_id": environment_id},
    )


async def add_mount(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, body: MountCreate, *, if_match: str | None
) -> tuple[MountView, int]:
    """Mount an environment for the thread's later runs; returns the mount and the new thread version.

    Replacing a mount is explicit: remove the name, then add it again.
    """
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        require_open(thread)
        [mount] = await mount_environments(session, thread, [body], principal_id=actor.id)
        _audit(session, actor, thread, "create", mount.name, mount.environment_id)
        await refresh_version(session, thread)
        return MountView.model_validate(mount), thread.version


async def remove_mount(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, name: str, *, if_match: str | None
) -> int:
    """Unmount for later runs; an active run keeps its frozen use. Returns the new thread version."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        mount = await session.get(ThreadEnvironmentRow, (thread.id, name))
        if mount is None:
            raise not_found("mount", name)
        await session.delete(mount)
        _audit(session, actor, thread, "delete", name, mount.environment_id)
        await refresh_version(session, thread)
        return thread.version
