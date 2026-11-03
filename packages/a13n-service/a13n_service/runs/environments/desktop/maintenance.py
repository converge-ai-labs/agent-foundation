"""Bounded Computer-control reconciliation in the existing control role."""

from datetime import timedelta

from a13n_logging import exception_details, get_logger
from sqlalchemy import delete, func, or_, select

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.sweeps import Sweep
from a13n_service.runs.environments.desktop import access, control
from a13n_service.runs.environments.desktop.tables import DesktopControlRow, DesktopViewerRow
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)


class Maintenance:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.inflight: set[str] = set()

    async def __call__(self) -> None:
        capacity = self.runtime.settings.control.sweep_batch - len(self.inflight)
        if capacity <= 0 or self.runtime.tasks.stopping:
            return
        async with transaction(self.runtime.storage) as session:
            current = await now(session)
            expired = select(DesktopViewerRow.id).where(DesktopViewerRow.expires_at <= current).limit(capacity)
            await session.execute(delete(DesktopViewerRow).where(DesktopViewerRow.id.in_(expired)))
            work = list(
                await session.scalars(
                    select(DesktopControlRow.id)
                    .where(
                        DesktopControlRow.control != "automation",
                        DesktopControlRow.id.not_in(self.inflight),
                        or_(DesktopControlRow.due_at.is_(None), DesktopControlRow.due_at <= current),
                        or_(DesktopControlRow.claim_until.is_(None), DesktopControlRow.claim_until <= current),
                    )
                    .order_by(func.coalesce(DesktopControlRow.due_at, current), DesktopControlRow.id)
                    .limit(capacity)
                )
            )
        for control_id in work:
            self.inflight.add(control_id)
            self.runtime.tasks.start(self._one(control_id), name=f"desktop-control-{control_id}")

    async def _one(self, control_id: str) -> None:
        try:
            # Reconcile owns the cross-worker claim and bounds its remote connection.
            await control.reconcile(self.runtime, control_id)
        except Exception as error:
            logger.warning(
                "Desktop control maintenance failed",
                extra={
                    "control_id": control_id,
                    "exception_details": exception_details(error),
                },
            )
            async with transaction(self.runtime.storage) as session:
                row = await lock(session, DesktopControlRow, control_id)
                current = await now(session)
                if row is not None and row.control != "automation" and (row.due_at is None or row.due_at <= current):
                    row.due_at = current + timedelta(seconds=access.RENEW_SECONDS)
        finally:
            self.inflight.discard(control_id)


def sweep(runtime: Runtime) -> Sweep:
    return Sweep(name="maintain_desktop_control", every=access.SCAN_SECONDS, run=Maintenance(runtime), timeout=30)
