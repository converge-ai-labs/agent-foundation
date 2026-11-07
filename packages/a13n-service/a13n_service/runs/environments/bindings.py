"""Desired Thread Session bindings and atomic Run admission; all operations are SQL-only."""

from collections.abc import Sequence

from a13n_harness.environment.session_recovery import EnvdSessionState
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, lock, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.resources.environment_instances.access import lock_environments
from a13n_service.runs.environments.schemas import SessionBindingView, SessionConfiguration
from a13n_service.runs.environments.tables import ThreadExecutionContextRow, ThreadSessionBindingRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import RunRow, ThreadRow, ToolExecutionRow
from a13n_service.runs.threads import get_thread, refresh_version, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope


class SessionHead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int
    configuration: SessionConfiguration
    session_version: int | None = None
    session: EnvdSessionState | None = None


def binding_key(mount: EnvironmentMount) -> tuple[str, str]:
    return mount.environment_id, mount.session_binding


async def ensure(
    session: AsyncSession,
    thread: ThreadRow,
    environment_id: str,
    name: str,
    configuration: SessionConfiguration | None = None,
) -> ThreadSessionBindingRow:
    """Mount creation selects an existing binding or creates its desired policy under the Thread lock."""
    row = await session.get(ThreadSessionBindingRow, (thread.id, environment_id, name))
    if row is None:
        row = ThreadSessionBindingRow(
            thread_id=thread.id,
            environment_id=environment_id,
            name=name,
            organization_id=thread.organization_id,
            workspace_id=thread.workspace_id,
            configuration=(configuration or SessionConfiguration()).model_dump(mode="json"),
        )
        session.add(row)
        await session.flush()
    elif configuration is not None and configuration != SessionConfiguration.model_validate(row.configuration):
        raise conflict("session_binding", name, "session_configuration_conflict")
    return row


async def require_idle(session: AsyncSession, thread_id: str, mount: EnvironmentMount) -> None:
    """Durable admitted work, rather than a live carrier or unexpired lease, prevents replacement."""
    selector = [{"environment_id": mount.environment_id, "session_binding": mount.session_binding}]
    run_id, execution_id = (
        await session.execute(
            select(
                select(RunRow.id)
                .where(
                    RunRow.thread_id == thread_id,
                    RunRow.status.in_(("accepted", "running", "waiting")),
                    RunRow.environment_mounts.contains(selector),
                )
                .limit(1)
                .scalar_subquery(),
                select(ToolExecutionRow.id)
                .where(
                    ToolExecutionRow.thread_id == thread_id,
                    ToolExecutionRow.completed_at.is_(None),
                    ToolExecutionRow.environment_mounts.contains(selector),
                )
                .limit(1)
                .scalar_subquery(),
            )
        )
    ).one()
    if run_id is not None or execution_id is not None:
        raise conflict(
            "session_binding", mount.session_binding, "environment_busy", run_id=run_id, execution_id=execution_id
        )


async def admit(session: AsyncSession, thread: ThreadRow, mounts: Sequence[EnvironmentMount]) -> list[EnvironmentMount]:
    """Freeze every binding version in the same acceptance transaction, without opening a Session."""
    from a13n_service.runs.environments.contexts import prepare

    await prepare(session, thread)
    context = await lock(session, ThreadExecutionContextRow, thread.id)
    assert context is not None
    selected: dict[tuple[str, str], SessionConfiguration] = {}
    for mount in mounts:
        key = binding_key(mount)
        if key in selected and selected[key] != mount.session_configuration:
            raise conflict("session_binding", mount.session_binding, "session_configuration_conflict")
        selected[key] = mount.session_configuration
    frozen = []
    for mount in mounts:
        row = await session.get(ThreadSessionBindingRow, (thread.id, *binding_key(mount)))
        if row is None:
            row = await ensure(
                session, thread, mount.environment_id, mount.session_binding, mount.session_configuration
            )
        head = SessionHead.model_validate(row.head) if row.head is not None else None
        if mount.session_version:
            if (
                head is None
                or head.version != mount.session_version
                or head.configuration != mount.session_configuration
            ):
                raise conflict("session_binding", mount.session_binding, "session_binding_changed")
        elif head is None:
            head = SessionHead(version=1, configuration=mount.session_configuration)
        elif head.configuration != mount.session_configuration:
            await require_idle(session, thread.id, mount)
            head = head.model_copy(update={"version": head.version + 1, "configuration": mount.session_configuration})
        assert head is not None
        row.head = head.model_dump(mode="json")
        frozen.append(mount.model_copy(update={"session_version": head.version}))
    return frozen


async def get_binding(
    storage: Storage, actor: Principal, workspace_id: str, thread_id: str, environment_id: str, name: str
) -> tuple[SessionBindingView, int]:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        thread = await get_thread(session, scope.workspace_id, thread_id)
        row = await session.get(ThreadSessionBindingRow, (thread.id, environment_id, name))
        if row is None:
            raise not_found("session_binding", name)
        return SessionBindingView.model_validate(row), thread.version


async def update_binding(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    environment_id: str,
    name: str,
    configuration: SessionConfiguration,
    *,
    if_match: str | None,
) -> tuple[SessionBindingView, int]:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        require_open(thread)
        await lock_environments(session, scope.workspace_id, [environment_id], principal_id=actor.id)
        row = await session.get(ThreadSessionBindingRow, (thread.id, environment_id, name))
        if row is None:
            raise not_found("session_binding", name)
        if configuration != SessionConfiguration.model_validate(row.configuration):
            row.configuration = configuration.model_dump(mode="json")
            row.version += 1
            # Desired policy is a Thread edit. Admission alone changes the active Session head.
            thread.version += 1
        record(
            session,
            WorkspaceScope(thread.organization_id, thread.workspace_id),
            actor_id=actor.id,
            action="thread_session_binding.update",
            target_kind="thread",
            target_id=thread.id,
            details={"environment_id": environment_id, "name": name, "version": row.version},
        )
        await session.flush()
        await refresh_version(session, thread)
        return SessionBindingView.model_validate(row), thread.version
