"""Change desired resources while preserving historical Run bindings."""

from collections import deque
from enum import Enum

from a13n_harness.environment.models import EnvironmentPermissionSet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict, invalid
from a13n_service.resources.egress_credentials.service import validate_references
from a13n_service.resources.environment_instances.access import lock_environments
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.service import replace_binding
from a13n_service.runs.environments import bindings, mounts
from a13n_service.runs.environments.desktop.delivery import suppress
from a13n_service.runs.environments.lifecycle import reserve
from a13n_service.runs.environments.schemas import MountCreate, SessionConfiguration
from a13n_service.runs.environments.tables import ThreadEnvironmentRow, ThreadSessionBindingRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

from ._access import app_workspace, run_actor


class _Omitted(Enum):
    VALUE = 0


async def _lock_shared_tree(session: AsyncSession, root: ThreadRow) -> list[ThreadRow]:
    queue = deque([root])
    selected = []
    while queue:
        parent = queue.popleft()
        if parent.archived_at is None:
            selected.append(parent)
        # Spawn locks parent before inserting child. Leave unrelated edge types and forks untouched.
        children = (
            await session.scalars(
                select(ThreadRow.id)
                .where(
                    ThreadRow.workspace_id == root.workspace_id,
                    ThreadRow.origin_thread_id == parent.id,
                    ThreadRow.origin == "child",
                    ThreadRow.agent_snapshot.is_(None),
                )
                .order_by(ThreadRow.id)
            )
        ).all()
        for child_id in children:
            child = await get_thread(session, root.workspace_id, child_id, lock="acceptance", history=False)
            if (await mounts.child_environment_policy(session, child)).mode == "shared":
                queue.append(child)
    return selected


async def _require_quiescent(session: AsyncSession, threads: list[ThreadRow]) -> None:
    for thread in threads:
        last = await session.get(RunRow, thread.last_run_id) if thread.last_run_id is not None else None
        pending = (
            select(InboxEntryRow.id)
            .where(
                InboxEntryRow.thread_id == thread.id,
                InboxEntryRow.status == "pending",
            )
            .exists()
        )
        if (
            thread.current_run_id
            or (last is not None and last.status == "waiting")
            or await session.scalar(select(pending))
        ):
            raise conflict("thread", thread.id, "thread_resources_busy")


async def _replace_primary(
    session: AsyncSession,
    actor: Principal,
    thread: ThreadRow,
    target: str | None,
    configuration: SessionConfiguration | _Omitted,
) -> None:
    old = await session.get(ThreadEnvironmentRow, (thread.id, mounts.PRIMARY))
    if old is None and target is None:
        return
    name = old.session_binding if old is not None else "default"
    if old is not None:
        await bindings.require_idle(
            session,
            thread.id,
            EnvironmentMount(
                name=mounts.PRIMARY,
                environment_id=old.environment_id,
                session_binding=name,
            ),
        )
    if target is not None:
        binding = await session.get(ThreadSessionBindingRow, (thread.id, target, name))
        desired = configuration if isinstance(configuration, SessionConfiguration) else None
        if desired is None and old is not None and old.environment_id != target:
            prior = await session.get_one(ThreadSessionBindingRow, (thread.id, old.environment_id, name))
            desired = SessionConfiguration.model_validate(prior.configuration)
        if desired is not None:
            await validate_references(
                session,
                actor,
                WorkspaceScope(thread.organization_id, thread.workspace_id),
                desired.credential_references,
            )
        if binding is None:
            await bindings.ensure(session, thread, target, name, desired)
        elif desired is not None and desired != SessionConfiguration.model_validate(binding.configuration):
            if old is None or old.environment_id != target:
                await bindings.require_idle(
                    session,
                    thread.id,
                    EnvironmentMount(
                        name=mounts.PRIMARY,
                        environment_id=target,
                        session_binding=name,
                    ),
                )
            binding.configuration = desired.model_dump(mode="json")
            binding.version += 1
            thread.version += 1
    await suppress(session, thread.id, mount_name=mounts.PRIMARY)
    if old is not None and target != old.environment_id:
        await session.delete(old)
        await session.flush()
    if target is not None and (old is None or target != old.environment_id):
        mount = MountCreate(name=mounts.PRIMARY, environment_id=target, session_binding=name)
        if old is not None:
            mount = MountCreate(
                name=mounts.PRIMARY,
                environment_id=target,
                session_binding=name,
                working_directory=old.working_directory,
                permission_ceiling=EnvironmentPermissionSet.model_validate(old.permission_ceiling),
            )
        await mounts.mount_environments(session, thread, [mount], principal=actor)


async def update_thread_resources(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    principal_id: str,
    root_thread_id: str,
    environment_id: str | _Omitted | None = _Omitted.VALUE,
    environment_template_id: str | _Omitted | None = _Omitted.VALUE,
    session_configuration: SessionConfiguration | _Omitted | None = _Omitted.VALUE,
    browser_id: str | _Omitted | None = _Omitted.VALUE,
    template_name: str | None = None,
) -> None:
    """Replace desired primary mounts for the native shared subtree; Browser edits affect only the root.

    Omitted values preserve the corresponding selection; None clears a Browser or Environment, or resets
    the Session configuration. A template reserves a new primary Environment shared by the selected Threads.
    """
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    if isinstance(environment_id, str) and isinstance(environment_template_id, str):
        raise invalid("environment_template_id", "mutually_exclusive_with_environment_id")
    root = await get_thread(session, workspace_id, root_thread_id, lock="acceptance", history=False)
    require_open(root)
    environment_edit = (
        environment_id is not _Omitted.VALUE
        or environment_template_id is not _Omitted.VALUE
        or session_configuration is not _Omitted.VALUE
    )
    threads = await _lock_shared_tree(session, root) if environment_edit else []
    if environment_edit:
        await _require_quiescent(session, threads)
    if not isinstance(browser_id, _Omitted):
        await replace_binding(
            session, actor, scope, root, BrowserBinding(browser_id=browser_id) if browser_id else None
        )
    if not environment_edit:
        return
    if isinstance(environment_template_id, str):
        row = await reserve(
            session,
            actor,
            scope,
            environment_template_id,
            limit=runtime.settings.environments.managed_count,
            name=template_name,
        )
        environment_id = row.id
    elif environment_template_id is None and isinstance(environment_id, _Omitted):
        environment_id = None
    if session_configuration is None:
        session_configuration = SessionConfiguration()
    targets = []
    for thread in threads:
        old = await session.get(ThreadEnvironmentRow, (thread.id, mounts.PRIMARY))
        targets.append(
            (old.environment_id if old is not None else None)
            if isinstance(environment_id, _Omitted)
            else environment_id
        )
    if isinstance(session_configuration, SessionConfiguration):
        if targets[0] is None:
            raise invalid("session_configuration", "requires_primary_environment")
    await lock_environments(
        session, workspace_id, [target for target in targets if target is not None], principal_id=actor.id
    )
    for thread, target in zip(threads, targets, strict=True):
        await _replace_primary(session, actor, thread, target, session_configuration)
    await session.flush()
