"""Service admission at the Harness operation boundary. SQL never spans provider I/O."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from a13n_harness.environment.models import FILE_READ_ACTIONS, EnvironmentAction, EnvironmentError, EnvironmentState
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock, transaction
from a13n_service.infra.errors import conflict
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow


def target_key(state: EnvironmentState | None) -> str | None:
    if state is None or not isinstance(state.state, dict):
        return None
    field = {"e2b": "sandbox_id", "docker": "container_id"}.get(state.provider_key)
    native = state.state.get(field) if field else None
    if not isinstance(native, str) or not native:
        return None
    # Native IDs are globally allocated. Treat ambiguous account aliases conservatively
    # as the same target rather than allowing a second controller through another account.
    return hashlib.sha256(f"{state.provider_key}:{native}".encode()).hexdigest()


def environment_key(environment: EnvironmentRow) -> str | None:
    return target_key(Handle.model_validate(environment.handle).state) if environment.handle else None


async def target(session: AsyncSession, key: str, *, environment_id: str, run_id: str) -> DesktopTargetRow:
    existing = await lock(session, DesktopTargetRow, key)
    if existing is not None:
        return existing
    environment = await session.get(EnvironmentRow, environment_id)
    assert environment is not None
    identities = await aliases(session, environment)
    # Pre-upgrade history has no per-operation evidence. Do not infer it was quiescent.
    historical = exists().where(
        RunRow.id != run_id,
        or_(*(RunRow.environment_mounts.contains([{"environment_id": identity}]) for identity in identities)),
    )
    await session.execute(insert(DesktopTargetRow).values(key=key, unmanaged_input=historical).on_conflict_do_nothing())
    result = await lock(session, DesktopTargetRow, key)
    assert result is not None
    return result


async def aliases(session: AsyncSession, environment: EnvironmentRow) -> list[str]:
    handle = Handle.model_validate(environment.handle)
    state = handle.state
    if state is None or not isinstance(state.state, dict):
        return [environment.id]
    field = {"e2b": "sandbox_id", "docker": "container_id"}.get(state.provider_key)
    if field is None:
        return [environment.id]
    return list(
        (
            await session.scalars(
                select(EnvironmentRow.id).where(
                    EnvironmentRow.handle["state"]["provider_key"].astext == state.provider_key,
                    EnvironmentRow.handle["state"]["state"][field].astext == state.state[field],
                )
            )
        ).all()
    )


async def admit_mounts(
    session: AsyncSession,
    environments: list[EnvironmentRow],
    *,
    resume_run_id: str | None = None,
    execution_run_id: str | None = None,
) -> None:
    keys = sorted({key for row in environments if (key := environment_key(row))})
    for key in keys:
        environment = next(row for row in environments if environment_key(row) == key)
        # Materialize the lock even before the first operation: aliases may use
        # different Environment rows while racing the first assistance request.
        row = await target(
            session,
            key,
            environment_id=environment.id,
            run_id=execution_run_id or resume_run_id or "",
        )
        if row.holder_id is None:
            continue
        assistance = await session.get(DesktopAssistanceRow, row.holder_id)
        if assistance is not None and assistance.state == "consumed" and assistance.successor_id == execution_run_id:
            if any(
                environment_key(environment) == key and environment.status != "ready" for environment in environments
            ):
                raise conflict("environment", "desktop", "desktop_target_not_running")
            continue
        if (
            assistance is None
            or assistance.run_id != resume_run_id
            or assistance.state not in {"ready_to_resume", "abandoned"}
        ):
            raise conflict("environment", "desktop", "desktop_assistance_in_use")


@asynccontextmanager
async def operation(
    runtime: Runtime, lease: Lease, environment_id: str, state: EnvironmentState | None, action: EnvironmentAction
) -> AsyncIterator[None]:
    key = target_key(state)
    if key is None:
        yield
        return
    async with transaction(runtime.storage) as session:
        await lock_lease(session, lease)
        row = await target(session, key, environment_id=environment_id, run_id=lease.run_id)
        assistance = await session.get(DesktopAssistanceRow, row.holder_id) if row.holder_id else None
        if row.holder_id is not None:
            if assistance is None or assistance.state != "consumed" or assistance.successor_id != lease.run_id:
                raise EnvironmentError("Desktop is reserved for human assistance.", code="environment_denied")
            if action not in {EnvironmentAction.COMPUTER_OBSERVE, EnvironmentAction.COMPUTER_DESCRIBE}:
                raise EnvironmentError(
                    "Observe the desktop and replan before sending input.", code="environment_denied"
                )
        if action in {
            EnvironmentAction.SHELL_EXEC,
            EnvironmentAction.PROCESS_START,
            EnvironmentAction.PROCESS_WRITE_STDIN,
        }:
            row.unmanaged_input = True
        row.inflight += 1
    completed = False
    read_only = action in FILE_READ_ACTIONS | {
        EnvironmentAction.COMPUTER_OBSERVE,
        EnvironmentAction.COMPUTER_DESCRIBE,
        EnvironmentAction.PROCESS_LIST,
        EnvironmentAction.PROCESS_INSPECT,
        EnvironmentAction.PROCESS_READ_OUTPUT,
        EnvironmentAction.OUTPUT_READ,
        EnvironmentAction.PORT_INSPECT,
        EnvironmentAction.PORT_WAIT,
    }
    try:
        yield
        completed = True
    finally:
        # A failed mutating dispatch may still be acting externally. Failed reads
        # cannot inject input and must not permanently poison a target reservation.
        if completed or read_only:
            with anyio.CancelScope(shield=True):
                with anyio.fail_after(5):
                    async with transaction(runtime.storage) as session:
                        row = await lock(session, DesktopTargetRow, key)
                        assert row is not None
                        row.inflight -= 1
                        if completed and row.holder_id and action == EnvironmentAction.COMPUTER_OBSERVE:
                            assistance = await session.get(DesktopAssistanceRow, row.holder_id)
                            if (
                                assistance is not None
                                and assistance.state == "consumed"
                                and assistance.successor_id == lease.run_id
                            ):
                                assistance.observed = True


async def replan(runtime: Runtime, lease: Lease) -> None:
    """Only the successor's next model boundary releases its fresh-observation gate."""
    async with transaction(runtime.storage) as session:
        await lock_lease(session, lease)
        rows = (
            await session.scalars(
                select(DesktopAssistanceRow).where(
                    DesktopAssistanceRow.successor_id == lease.run_id,
                    DesktopAssistanceRow.state == "consumed",
                    DesktopAssistanceRow.observed.is_(True),
                )
            )
        ).all()
        for assistance in sorted(rows, key=lambda row: row.target_key):
            row = await lock(session, DesktopTargetRow, assistance.target_key)
            if row is not None and row.holder_id == assistance.id and row.inflight == 0:
                row.holder_id = None
