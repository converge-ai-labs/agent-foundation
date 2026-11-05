"""Desired thread mounts and the immutable mount set a run freezes at acceptance.

The frozen `runs.environment_mounts` of accepted and running runs is the durable active-use evidence that
stop and destroy check under the environment row lock; acceptance share-locks the same rows before installing it.
Desired mount edits are thread operations under the thread `If-Match`, and affect later acceptance only; a
new thread or fork takes its initial mounts through the same checks before its first acceptance.
"""

from collections.abc import Mapping, Sequence

from a13n_harness.environment.models import EnvironmentPermissionSet
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.resources.agents import stored
from a13n_service.resources.agents.schemas import ChildEnvironmentPolicy
from a13n_service.resources.agents.service import load_revision
from a13n_service.runs.environments.lifecycle import (
    LOST,
    confirmed_loss,
    latest_replacement,
    require_usable_state,
    reserve,
    superseded,
)
from a13n_service.runs.environments.schemas import (
    DEFAULT_MOUNT_PERMISSIONS,
    MAX_MOUNTS,
    Handle,
    MountCreate,
    MountPage,
    MountUpdate,
    MountView,
)
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.schemas import EnvironmentMount, RunOptions
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
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


async def primary_mount(session: AsyncSession, thread_id: str) -> EnvironmentMount | None:
    """The thread's desired mount of its primary sandbox."""
    row = await session.get(ThreadEnvironmentRow, (thread_id, PRIMARY))
    return None if row is None else EnvironmentMount.model_validate(row, from_attributes=True)


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


async def interactive_mounts(session: AsyncSession, thread: ThreadRow) -> list[EnvironmentMount]:
    """Current bindings for desktop control and handback, including a Run-only primary.

    A newer Run supersedes the temporary selection. Desired mounts remain live for
    all other names and for Threads without an explicit Run selection.
    """
    mounts = [
        EnvironmentMount.model_validate(row, from_attributes=True) for row in await desired_mounts(session, thread.id)
    ]
    run_id = thread.current_run_id or thread.last_run_id
    run = await session.scalar(select(RunRow).where(RunRow.id == run_id)) if run_id else None
    if run is not None and RunOptions.model_validate(run.options).environment is not None:
        mounts = [mount for mount in mounts if mount.name != PRIMARY]
        if run.status != "cancelled" and run.cancel_requested_at is None:
            mounts.extend(
                EnvironmentMount.model_validate(mount) for mount in run.environment_mounts if mount["name"] == PRIMARY
            )
    return mounts


def _mount(
    thread: ThreadRow,
    environment_id: str,
    name: str,
    working_directory: str | None,
    permission_ceiling: EnvironmentPermissionSet = DEFAULT_MOUNT_PERMISSIONS,
) -> ThreadEnvironmentRow:
    return ThreadEnvironmentRow(
        thread_id=thread.id,
        environment_id=environment_id,
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        name=name,
        working_directory=working_directory,
        permission_ceiling=permission_ceiling.model_dump(mode="json"),
    )


async def shared_mounts(session: AsyncSession, origin: ThreadRow) -> list[MountCreate]:
    """The origin's desired mounts, which a fork shares unless it asks for fresh environments; the caller holds
    the origin thread lock."""
    return [
        MountCreate.model_validate(mount, from_attributes=True) for mount in await desired_mounts(session, origin.id)
    ]


async def adopt_mounts(session: AsyncSession, thread: ThreadRow, mounts: Sequence[EnvironmentMount]) -> None:
    """A child thread mounts its parent's: what the parent run froze when it was spawned, or later the parent thread's
    primary. The run that starts it locks and checks them."""
    for mount in mounts:
        session.add(_mount(thread, mount.environment_id, mount.name, mount.working_directory, mount.permission_ceiling))
    await session.flush()


def require_usable(environment: EnvironmentRow, principal_id: str) -> None:
    """New use refuses retiring instances, unresolved permanent failures and other principals' devices."""
    if environment.owner_principal_id not in {None, principal_id}:
        raise ServiceError("forbidden", "Private environments are usable only by their owner", {"id": environment.id})
    if environment.status in {"deleting", "deleted"}:
        raise conflict("environment", environment.id, f"environment_{environment.status}")
    require_usable_state(environment)


async def _locked_environments(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str]
) -> dict[str, EnvironmentRow]:
    """Share-lock the selected instances in ID order before deciding whether each can be mounted."""
    ids = sorted(set(environment_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.workspace_id == workspace_id, EnvironmentRow.id.in_(ids))
            .order_by(EnvironmentRow.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {row.id: row for row in rows}
    for environment_id in ids:
        environment = found.get(environment_id)
        if environment is None:
            raise not_found("environment", environment_id)
        # An exact selection of a lost ID keeps its refusal even after maintenance retires the provider handle.
        if environment.status in {"deleting", "deleted"} and await session.scalar(select(superseded(environment_id))):
            raise conflict("environment", environment_id, LOST.code)
    return found


async def lock_environments(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str], *, principal_id: str
) -> None:
    for environment in (await _locked_environments(session, workspace_id, environment_ids)).values():
        require_usable(environment, principal_id)


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
    rows = [
        _mount(thread, mount.environment_id, mount.name, mount.working_directory, mount.permission_ceiling)
        for mount in mounts
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def reserve_primary(
    session: AsyncSession,
    principal: Principal,
    thread: ThreadRow,
    *,
    template_id: str,
    limit: int,
) -> EnvironmentRow:
    """Reserve the primary under the Thread lock."""
    scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
    environment = await reserve(session, principal, scope, template_id, limit=limit)
    session.add(_mount(thread, environment.id, PRIMARY, None))
    await session.flush()
    return environment


async def child_environment_policy(session: AsyncSession, thread: ThreadRow) -> ChildEnvironmentPolicy:
    """The named edge as frozen by the spawning Run."""
    origin = await session.get(RunRow, thread.origin_run_id)
    assert origin is not None and thread.origin_thread_id is not None and thread.subagent is not None
    assert origin.agent_revision_id is not None
    revision = await load_revision(session, origin.agent_id, origin.agent_revision_id)
    override = stored.require(RunOptions, origin.options, kind="run", id=origin.id).overrides
    spawning = stored.require_effective(revision.config, override, revision_id=revision.revision_id)
    return spawning.subagents.named[thread.subagent].environment


async def _recovery_enabled(session: AsyncSession, thread: ThreadRow) -> bool:
    if thread.agent_snapshot is not None:
        return False
    if thread.origin == "child" and (await child_environment_policy(session, thread)).mode != "dedicated":
        return False
    return bool(
        await session.scalar(
            select(SessionRow.auto_recreate_lost_environment).where(SessionRow.id == thread.session_id)
        )
    )


async def _recover_primary(
    session: AsyncSession,
    principal: Principal,
    thread: ThreadRow,
    mount: ThreadEnvironmentRow,
    latest: EnvironmentRow,
    limit: int,
) -> None:
    scope = WorkspaceScope(thread.organization_id, thread.workspace_id)
    if confirmed_loss(latest):
        assert latest.template_id is not None
        latest = await reserve(session, principal, scope, latest.template_id, limit=limit, replaces=latest)
    require_usable(latest, principal.id)
    record(
        session,
        scope,
        actor_id=principal.id,
        action="thread_environment.recreate",
        target_kind="thread",
        target_id=thread.id,
        details={"name": mount.name, "previous_environment_id": mount.environment_id, "environment_id": latest.id},
    )
    mount.environment_id = latest.id


async def freeze_mounts(
    session: AsyncSession,
    thread: ThreadRow,
    *,
    principal: Principal,
    primary: EnvironmentMount | None = None,
    recreation_limit: int | None,
) -> list[dict]:
    """Freeze desired mounts with an optional Run-only primary; lock only the selected instances in ID order."""
    # Read the primary's loss code with its desired mount so a successor can join the same ID-ordered lock set.
    query = (
        select(ThreadEnvironmentRow, EnvironmentRow.failure["code"].astext)
        .outerjoin(
            EnvironmentRow,
            and_(ThreadEnvironmentRow.name == PRIMARY, ThreadEnvironmentRow.environment_id == EnvironmentRow.id),
        )
        .where(ThreadEnvironmentRow.thread_id == thread.id)
    )
    if primary is not None:
        query = query.where(ThreadEnvironmentRow.name != PRIMARY)
    recovery_id = None
    selected: list[ThreadEnvironmentRow | EnvironmentMount] = []
    for mount, failure_code in await session.execute(query):
        selected.append(mount)
        if failure_code == LOST.code and recreation_limit is not None and await _recovery_enabled(session, thread):
            recovery_id = await latest_replacement(session, thread.workspace_id, mount.environment_id)
    if primary is not None:
        if any(mount.environment_id == primary.environment_id for mount in selected):
            raise conflict("environment", primary.environment_id, "already_mounted")
        selected.append(primary)
    ids = [mount.environment_id for mount in selected]
    if recovery_id is not None:
        ids.append(recovery_id)
    locked = await _locked_environments(session, thread.workspace_id, ids)
    frozen = []
    for mount in sorted(selected, key=lambda mount: mount.name):
        environment = locked[mount.environment_id]
        if recovery_id is not None and mount.name == PRIMARY and confirmed_loss(environment):
            assert isinstance(mount, ThreadEnvironmentRow) and recreation_limit is not None
            if any(other.name != PRIMARY and other.environment_id == recovery_id for other in selected):
                raise conflict("environment", recovery_id, "already_mounted")
            await _recover_primary(session, principal, thread, mount, locked[recovery_id], recreation_limit)
        else:
            require_usable(environment, principal.id)
        frozen.append(EnvironmentMount.model_validate(mount, from_attributes=True).model_dump(mode="json"))
    return frozen


def _view(mount: ThreadEnvironmentRow, environment: EnvironmentRow) -> MountView:
    return MountView(
        name=mount.name,
        environment_id=mount.environment_id,
        working_directory=mount.working_directory,
        permission_ceiling=EnvironmentPermissionSet.model_validate(mount.permission_ceiling),
        desktop_enabled=Handle.model_validate(environment.handle).desktop_enabled if environment.handle else False,
    )


async def list_mounts(storage: Storage, actor: Principal, workspace_id: str, thread_id: str) -> tuple[MountPage, int]:
    """The thread's desired mounts, and the thread version their edits must name."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        rows = await session.execute(
            select(ThreadEnvironmentRow, EnvironmentRow)
            .join(EnvironmentRow, ThreadEnvironmentRow.environment_id == EnvironmentRow.id)
            .where(ThreadEnvironmentRow.thread_id == thread.id)
            .order_by(ThreadEnvironmentRow.name)
        )
        return MountPage(items=[_view(mount, environment) for mount, environment in rows]), thread.version


def _audit(
    session: AsyncSession,
    actor: Principal,
    thread: ThreadRow,
    verb: str,
    name: str,
    environment_id: str,
    *,
    permission_ceiling: EnvironmentPermissionSet | None = None,
) -> None:
    record(
        session,
        WorkspaceScope(thread.organization_id, thread.workspace_id),
        actor_id=actor.id,
        action=f"thread_environment.{verb}",
        target_kind="thread",
        target_id=thread.id,
        details={
            "name": name,
            "environment_id": environment_id,
            **(
                {"permission_ceiling": permission_ceiling.model_dump(mode="json")}
                if permission_ceiling is not None
                else {}
            ),
        },
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
        environment = await session.get(EnvironmentRow, mount.environment_id)
        assert environment is not None
        return _view(mount, environment), thread.version


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
        from a13n_service.runs.environments.desktop.delivery import suppress

        await suppress(session, thread.id, mount_name=name)
        await session.delete(mount)
        _audit(session, actor, thread, "delete", name, mount.environment_id)
        await refresh_version(session, thread)
        return thread.version


async def update_mount(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    name: str,
    body: MountUpdate,
    *,
    if_match: str | None,
) -> tuple[MountView, int]:
    """Change the exact action ceiling for later runs only."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        require_open(thread)
        mount = await session.get(ThreadEnvironmentRow, (thread.id, name))
        if mount is None:
            raise not_found("mount", name)
        await lock_environments(session, scope.workspace_id, [mount.environment_id], principal_id=actor.id)
        ceiling = body.permission_ceiling.model_dump(mode="json")
        if mount.permission_ceiling != ceiling:
            from a13n_service.runs.environments.desktop.delivery import suppress

            await suppress(session, thread.id, mount_name=name)
        mount.permission_ceiling = ceiling
        await session.flush()
        _audit(
            session,
            actor,
            thread,
            "update",
            mount.name,
            mount.environment_id,
            permission_ceiling=body.permission_ceiling,
        )
        await refresh_version(session, thread)
        environment = await session.get(EnvironmentRow, mount.environment_id)
        assert environment is not None
        return _view(mount, environment), thread.version
