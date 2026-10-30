"""Bounded rotating lifecycle/control reconciliation in the existing control role."""

import asyncio
from datetime import timedelta

import anyio
from a13n_logging import exception_details, get_logger
from sqlalchemy import func, or_, select

from a13n_service.infra.audit import record
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.sweeps import Sweep
from a13n_service.runs.browsers import control, lifecycle
from a13n_service.runs.browsers.schemas import Viewer
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
            lifecycle.release_intent(row)
            record(
                session,
                WorkspaceScope(row.organization_id, row.workspace_id),
                actor_id=None,
                action="browser.release",
                target_kind="browser",
                target_id=row.id,
                details={"reason": "idle"},
            )
        inspect_ready = row.source == "managed"
    await lifecycle.reconcile(runtime, workspace_id, browser_id, inspect_ready=inspect_ready)


class ControlMaintenance:
    """Lease due work independently of the lifecycle scan and provider latency."""

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.inflight: set[asyncio.Task[None]] = set()

    async def __call__(self) -> None:
        settings = self.runtime.settings.providers
        capacity = self.runtime.settings.control.sweep_batch - len(self.inflight)
        if capacity <= 0 or self.runtime.tasks.stopping:
            return
        async with transaction(self.runtime.storage) as session:
            current = await now(session)
            rows = list(
                await session.scalars(
                    select(BrowserRow)
                    .where(
                        BrowserRow.control != "automation",
                        BrowserRow.status.not_in(("deleted", "lost", "deleting")),
                        or_(BrowserRow.control_due_at.is_(None), BrowserRow.control_due_at <= current),
                    )
                    .order_by(func.coalesce(BrowserRow.control_due_at, current), BrowserRow.id)
                    .limit(capacity)
                    .with_for_update(skip_locked=True)
                )
            )
            # A reservation covers one provider claim budget, not an entire batch.
            lease = current + timedelta(seconds=2 * settings.operation_seconds + 25)
            work = [(row.workspace_id, row.id, row.generation, row.episode) for row in rows]
            for row in rows:
                row.control_due_at = lease
        for workspace_id, browser_id, generation, episode in work:
            task = self.runtime.tasks.start(
                self._bounded_one(workspace_id, browser_id, generation, episode, lease),
                name=f"browser-control-{browser_id}",
            )
            self.inflight.add(task)
            task.add_done_callback(self.inflight.discard)

    async def _bounded_one(self, workspace_id: str, browser_id: str, generation: int, episode: int, lease) -> None:
        async with asyncio.timeout(2 * self.runtime.settings.providers.operation_seconds + 30):
            await self._one(workspace_id, browser_id, generation, episode, lease)

    async def _one(self, workspace_id: str, browser_id: str, generation: int, episode: int, lease) -> None:
        try:
            await control.reconcile(self.runtime, workspace_id, browser_id, lease)
        except Exception as error:
            logger.warning(
                "Browser control maintenance failed",
                extra={"browser_id": browser_id, "exception_details": exception_details(error)},
            )
        finally:
            async with transaction(self.runtime.storage) as session:
                row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
                if row.generation == generation and row.episode == episode and row.control_due_at == lease:
                    current = await now(session)
                    retry = timedelta(seconds=control.maintenance_interval(self.runtime))
                    due = current + retry
                    if row.claim_until is not None and row.claim_until > current:
                        due = row.claim_until
                    elif row.control == "human" and row.viewer and row.control_expires_at:
                        grant = Viewer.model_validate(lifecycle.reveal(self.runtime, row, "viewer", row.viewer))
                        due = min(current + timedelta(seconds=30), row.control_expires_at)
                        if grant.expires_at < row.control_expires_at:
                            due = min(
                                due, max(current + retry, grant.expires_at - control.recovery_window(self.runtime))
                            )
                    row.control_due_at = due


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


def control_sweep(runtime: Runtime) -> Sweep:
    return Sweep(
        name="maintain_browser_control",
        every=control.maintenance_interval(runtime),
        run=ControlMaintenance(runtime),
        timeout=30,
    )


def sweep(runtime: Runtime) -> Sweep:
    settings = runtime.settings.providers
    return Sweep(
        name="maintain_browsers",
        every=settings.operation_scan_seconds,
        run=Maintenance(runtime),
        timeout=4 * settings.operation_seconds + 60,
    )
