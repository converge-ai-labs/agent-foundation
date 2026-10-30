"""Browser-only durable admission. Unknown calls never become drained by lease expiry."""

from dataclasses import dataclass

from a13n_harness import RunConfiguration
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl
from sqlalchemy import or_, select

from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.attempts import Lease
from a13n_service.runs.browsers import lifecycle
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserOperationRow, BrowserSelectionRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.tables import RunRow
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope

GUIDANCE = (
    "The browser is under human control. This call did not execute. "
    "A handback notice is registered for this Thread if control returns with verified availability. "
    "Cancellation before viewer grant suppresses that notice. Continue unrelated work or finish the turn; "
    "do not poll or retry. After handback, observe afresh and decide the next action."
)


@dataclass(frozen=True)
class Permit:
    id: str
    browser_id: str
    generation: int
    epoch: int
    provider_id: str | None
    selected_id: str | None
    configuration: RunConfiguration


async def admit(
    runtime: Runtime, lease: Lease, binding: BrowserBinding, *, inventory: bool = True, register_blocked: bool = True
) -> Permit:
    permit: Permit | None = None
    async with transaction(runtime.storage) as session:
        await lifecycle.lock_owners(session, (lease.thread_id,))
        run, principal = await lock_owner(session, runtime, lease)
        if run.browser_binding != binding.model_dump(mode="json"):
            raise conflict("browser", binding.browser_id, "browser_binding_changed")
        row = await lifecycle.find(session, lease.workspace_id, binding.browser_id, lock=True)
        lifecycle.usable(row)
        await lifecycle.authorize_use(
            session,
            principal,
            row,
            WorkspaceScope(lease.organization_id, lease.workspace_id),
            authority=ExecutionAuthority.model_validate(run.authority),
        )
        current = await now(session)
        if row.expires_at is not None and row.expires_at <= current:
            raise conflict("browser", row.id, "browser_expired")
        if row.control != "automation":
            if not register_blocked:
                raise BrowserHumanControl(GUIDANCE)
            registration = await session.scalar(
                select(BrowserBlockedRow).where(
                    BrowserBlockedRow.browser_id == row.id,
                    BrowserBlockedRow.generation == row.generation,
                    BrowserBlockedRow.episode == row.episode,
                    BrowserBlockedRow.thread_id == lease.thread_id,
                )
            )
            if registration is not None and registration.suppressed:
                # A new actual block establishes new provenance. Old queued hints must keep referring
                # to the retired identity, never to a renewed registration under another Run's authority.
                await session.delete(registration)
                await session.flush()
                registration = None
            if registration is None:
                session.add(
                    BrowserBlockedRow(
                        id=new_object_id("bblock"),
                        browser_id=row.id,
                        thread_id=lease.thread_id,
                        run_id=run.id,
                        generation=row.generation,
                        episode=row.episode,
                        expires_at=None,
                    )
                )
        else:
            selection = await session.get(BrowserSelectionRow, (row.id, lease.thread_id))
            selected_id = (
                selection.target_id if selection is not None and selection.generation == row.generation else None
            )
            if not inventory and selected_id is None:
                raise BrowserError("No selected page. List tabs, then explicitly select or open a tab.")
            target_id = None if inventory else selected_id
            pending = (
                select(BrowserOperationRow.id)
                .join(RunRow, RunRow.id == BrowserOperationRow.run_id)
                .where(
                    BrowserOperationRow.browser_id == row.id,
                    BrowserOperationRow.settled_at.is_(None),
                )
            )
            if target_id is not None:
                pending = pending.where(
                    or_(
                        BrowserOperationRow.target_id.is_(None),
                        BrowserOperationRow.target_id == target_id,
                        RunRow.thread_id == lease.thread_id,
                    )
                )
            if await session.scalar(pending.limit(1)) is not None:
                raise BrowserError("This browser target has an unsettled operation. No call was dispatched.")
            row.last_used_at = current
            row.observation_epoch += 1
            permit = Permit(
                new_object_id("bop"),
                row.id,
                row.generation,
                row.observation_epoch,
                row.provider_id,
                selected_id,
                RunOptions.model_validate(run.options).configuration or RunConfiguration(),
            )
            session.add(
                BrowserOperationRow(
                    id=permit.id,
                    browser_id=row.id,
                    run_id=run.id,
                    attempt_id=lease.attempt_id,
                    generation=row.generation,
                    target_id=target_id,
                )
            )
    # The registration must commit before the native capability returns its guidance.
    if permit is None:
        raise BrowserHumanControl(GUIDANCE)
    return permit


async def settle(runtime: Runtime, permit: Permit) -> None:
    """Called only after a known result or proof that no operation was dispatched."""
    async with transaction(runtime.storage) as session:
        row = await session.get(BrowserOperationRow, permit.id, with_for_update=True)
        if row is not None and row.settled_at is None:
            row.settled_at = await now(session)
