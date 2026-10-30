"""Computer-only admission and typed settlement; no transaction spans a provider call."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anyio
from a13n_harness.environment.computer import (
    ComputerActionResult,
    ComputerCall,
    ComputerClipboardWriteResult,
    ComputerScreenshot,
)
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError, EnvironmentState
from a13n_harness.tool_execution import current_execution
from sqlalchemy import select

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.attempts import Lease
from a13n_service.runs.environments.desktop import freshness, identity
from a13n_service.runs.environments.desktop.tables import ComputerOperationRow, DesktopBlockedRow, DesktopControlRow
from a13n_service.runs.environments.mounts import require_usable
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import ThreadRow
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
    id: str
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
            registration = await session.scalar(
                select(DesktopBlockedRow).where(
                    DesktopBlockedRow.control_id == row.id,
                    DesktopBlockedRow.generation == row.generation,
                    DesktopBlockedRow.episode == row.episode,
                    DesktopBlockedRow.thread_id == lease.thread_id,
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
                        thread_id=lease.thread_id,
                        run_id=run.id,
                        mount=mount.model_dump(mode="json"),
                        generation=row.generation,
                        episode=row.episode,
                    )
                )
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
            permit = Permit(new_object_id("cop"), row.id, row.generation, row.episode, source)
            record = current_execution()
            session.add(
                ComputerOperationRow(
                    id=permit.id,
                    control_id=row.id,
                    environment_id=environment.id,
                    run_id=run.id,
                    attempt_id=lease.attempt_id,
                    execution_id=record.execution_id if record is not None else None,
                    generation=row.generation,
                    episode=row.episode,
                    action=call.action.value,
                )
            )
    if permit is None:
        # Raising inside the transaction would lose the actual blocked registration.
        raise EnvironmentError(
            GUIDANCE,
            code="environment_denied",
            details={"reason": "desktop_human_control", "dispatch_stage": "pre_dispatch", "hint": GUIDANCE},
        )
    return permit


@dataclass(frozen=True)
class Settlement:
    settled: bool
    effect: str | None
    cleanup_complete: bool | None


def evidence(call: ComputerCall) -> Settlement:
    if not call.dispatched:
        return Settlement(True, "not_executed", True)
    result = call.result
    if isinstance(result, ComputerActionResult):
        return Settlement(result.input_cleanup_complete, result.effect, result.input_cleanup_complete)
    if isinstance(result, ComputerClipboardWriteResult):
        return Settlement(result.cleanup_complete, result.effect, result.cleanup_complete)
    if call.error is not None and call.error.details.get("dispatch_stage") == "pre_dispatch":
        return Settlement(True, "not_executed", True)
    if call.action in freshness.READ_ACTIONS:
        return Settlement(True, None, True)
    return Settlement(False, "unknown", None)


async def settle(runtime: Runtime, permit: Permit, call: ComputerCall) -> None:
    result = evidence(call)
    async with transaction(runtime.storage) as session:
        control = await lock(session, DesktopControlRow, permit.control_id)
        operation = await lock(session, ComputerOperationRow, permit.id)
        assert control is not None and operation is not None
        operation.dispatched = call.dispatched
        operation.effect = result.effect
        operation.cleanup_complete = result.cleanup_complete
        if result.settled:
            operation.settled_at = await now(session)
        observed = (
            permit.source is not None
            and call.action == EnvironmentAction.COMPUTER_OBSERVE
            and isinstance(call.result, ComputerScreenshot)
            and control.control == "automation"
            and (control.generation, control.episode) == (permit.generation, permit.episode)
        )
    if observed:
        assert permit.source is not None and isinstance(call.result, ComputerScreenshot)
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
    try:
        yield
    finally:
        with anyio.CancelScope(shield=True), anyio.fail_after(5):
            await settle(runtime, permit, call)
