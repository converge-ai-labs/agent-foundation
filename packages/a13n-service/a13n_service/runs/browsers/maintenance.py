"""Bounded rotating lifecycle/control reconciliation in the existing control role."""

from datetime import timedelta

import anyio
from a13n_logging import exception_details, get_logger
from sqlalchemy import select

from a13n_service.infra.audit import record
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.sweeps import Sweep
from a13n_service.runs.browsers import control, lifecycle
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


async def maintain(runtime: Runtime, workspace_id: str, browser_id: str) -> None:
    async with transaction(runtime.storage) as session:
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        if await lifecycle.expire(session, row):
            return
        current = await now(session)
        if (
            row.status in {"reserved", "ready"}
            and row.idle_seconds is not None
            and row.last_used_at + timedelta(seconds=row.idle_seconds) <= current
            and (row.claim_until is None or row.claim_until <= current)
            and not await lifecycle.in_use(session, row)
        ):
            row.status = "deleted" if row.status == "reserved" else "deleting"
            row.claim_id = row.claim_until = None
            record(
                session,
                WorkspaceScope(row.organization_id, row.workspace_id),
                actor_id=None,
                action="browser.release",
                target_kind="browser",
                target_id=row.id,
                details={"reason": "idle"},
            )
    await lifecycle.reconcile(runtime, workspace_id, browser_id, inspect_ready=True)
    await control.reconcile(runtime, workspace_id, browser_id)


class Maintenance:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.after: str | None = None

    async def __call__(self) -> None:
        query = select(BrowserRow.workspace_id, BrowserRow.id).where(BrowserRow.status.not_in(("deleted", "lost")))
        if self.after is not None:
            query = query.where(BrowserRow.id > self.after)
        async with short_session(self.runtime.storage) as session:
            rows = (
                await session.execute(query.order_by(BrowserRow.id).limit(self.runtime.settings.control.sweep_batch))
            ).all()
        self.after = rows[-1].id if rows else None
        async with anyio.create_task_group() as group:
            for workspace_id, browser_id in rows:
                group.start_soon(self._one, workspace_id, browser_id)

    async def _one(self, workspace_id: str, browser_id: str) -> None:
        try:
            await maintain(self.runtime, workspace_id, browser_id)
        except Exception as error:
            logger.warning(
                "Browser maintenance failed",
                extra={"browser_id": browser_id, "exception_details": exception_details(error)},
            )


def sweep(runtime: Runtime) -> Sweep:
    settings = runtime.settings.providers
    return Sweep(
        name="maintain_browsers",
        every=settings.operation_scan_seconds,
        run=Maintenance(runtime),
        timeout=4 * settings.operation_seconds + 60,
    )
