"""Prospective Computer admission and fresh-observation registration."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_harness.environment.computer import ComputerCall, ComputerScreenshot
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.attempts import Lease
from a13n_service.runs.environments.desktop import freshness, identity
from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow, DesktopControlRow
from a13n_service.runs.environments.mounts import require_usable
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.tool_executions.authority import lock_owner

GUIDANCE = (
    "The desktop Computer channel is under human control. This call did not execute. "
    "A handback notice is registered for this Thread if control returns with verified availability. "
    "Cancellation before an input grant suppresses that notice. Continue unrelated work or finish the turn; "
    "do not poll or retry. Shell, files, processes and other desktops remain available. "
    "After handback, observe afresh and make a new model decision before sending input."
)


@dataclass(frozen=True)
class Permit:
    control_id: str
    generation: int
    episode: int
    source: freshness.Invocation | None


async def admit(
    runtime: Runtime, lease: Lease, mount: EnvironmentMount, state: EnvironmentState, call: ComputerCall
) -> Permit:
    key = identity.target_key(state)
    assert key is not None
    permit = None
    async with transaction(runtime.storage) as session:
        await lock(session, ThreadRow, lease.thread_id)
        run, principal = await lock_owner(session, runtime, lease)
        if mount.model_dump(mode="json") not in run.environment_mounts:
            raise conflict("environment", mount.environment_id, "environment_binding_changed")
        environment = await lock(session, EnvironmentRow, mount.environment_id)
        if environment is None or identity.environment_key(environment) != key:
            raise conflict("environment", mount.environment_id, "desktop_target_changed")
        require_usable(environment, principal.id)
        row = await identity.target(session, key)
        if row.control != "automation":
            await _register(session, row, run, mount)
        else:
            source = freshness.current.get()
            if source is not None:
                source.require(row.id, row.generation, row.episode, call)
            elif row.episode > 0 and call.action not in freshness.READ_ACTIONS:
                raise EnvironmentError(
                    "Observe the desktop and make a new model decision before input.",
                    code="environment_denied",
                    details={
                        "reason": "desktop_observation_required",
                        "dispatch_stage": "pre_dispatch",
                        "hint": "Observe the desktop and make a new model decision before input.",
                    },
                )
            permit = Permit(row.id, row.generation, row.episode, source)
    if permit is None:
        # Raising inside the transaction would lose the actual blocked registration.
        raise EnvironmentError(
            GUIDANCE,
            code="environment_denied",
            details={"reason": "desktop_human_control", "dispatch_stage": "pre_dispatch", "hint": GUIDANCE},
        )
    return permit


async def _register(session: AsyncSession, row: DesktopControlRow, run: RunRow, mount: EnvironmentMount) -> None:
    """Both automatic and explicit registrations share the target/Thread episode key."""
    registration = await session.scalar(
        select(DesktopBlockedRow).where(
            DesktopBlockedRow.control_id == row.id,
            DesktopBlockedRow.generation == row.generation,
            DesktopBlockedRow.episode == row.episode,
            DesktopBlockedRow.thread_id == run.thread_id,
        )
    )
    if registration is not None and registration.suppressed:
        await session.delete(registration)
        await session.flush()
        registration = None
    if registration is None:
        session.add(
            DesktopBlockedRow(
                id=new_object_id("dblock"),
                control_id=row.id,
                thread_id=run.thread_id,
                run_id=run.id,
                mount=mount.model_dump(mode="json"),
                generation=row.generation,
                episode=row.episode,
            )
        )


async def register_handback(runtime: Runtime, lease: Lease, alias: str, state: EnvironmentState | None) -> bool:
    """Register this caller for the current takeover; False means admission is already open."""
    key = identity.target_key(state)
    if key is None:
        raise invalid("alias", "This mount has no known native desktop target; no handback notice was registered.")
    async with transaction(runtime.storage) as session:
        thread = await lock(session, ThreadRow, lease.thread_id)
        run, principal = await lock_owner(session, runtime, lease)
        if thread is None or thread.archived_at is not None:
            raise conflict("thread", lease.thread_id, "archived")
        mount = next(
            (EnvironmentMount.model_validate(item) for item in run.environment_mounts if item["name"] == alias), None
        )
        if mount is None:
            raise invalid("alias", "Select an Environment mount of the current Run.")
        if not any(action.value.startswith("environment.computer.") for action in mount.permission_ceiling.operations):
            raise invalid("alias", "This mount does not permit Computer use.")
        environment = await lock(session, EnvironmentRow, mount.environment_id)
        if environment is None or identity.environment_key(environment) != key:
            raise conflict("environment", mount.environment_id, "desktop_target_changed")
        require_usable(environment, principal.id)
        row = await identity.target(session, key)
        if row.control == "automation":
            return False
        await _register(session, row, run, mount)
        return True


async def register_observation(runtime: Runtime, permit: Permit, call: ComputerCall) -> None:
    if (
        permit.source is None
        or call.action != EnvironmentAction.COMPUTER_OBSERVE
        or not isinstance(call.result, ComputerScreenshot)
    ):
        return
    async with transaction(runtime.storage) as session:
        control = await lock(session, DesktopControlRow, permit.control_id)
        assert control is not None
        if control.control != "automation" or (control.generation, control.episode) != (
            permit.generation,
            permit.episode,
        ):
            return
    permit.source.observed(
        freshness.Observation(permit.control_id, permit.generation, permit.episode, call.result.observation),
        call.result.image_identifier,
    )


@asynccontextmanager
async def operation(
    runtime: Runtime, lease: Lease, mount: EnvironmentMount, state: EnvironmentState | None, call: ComputerCall
) -> AsyncGenerator[None]:
    if identity.target_key(state) is None:
        # Providers without native desktop access cannot be taken over by Service.
        yield
        return
    assert state is not None
    permit = await admit(runtime, lease, mount, state, call)
    yield
    await register_observation(runtime, permit, call)
