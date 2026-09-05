"""Worker-owned lifecycle maintenance, independent of Plugin Runtime locks."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from a13n_logging import get_logger
from sqlalchemy import or_, select, update

from a13n_service.storage import transaction

from .identity import local_backend_eligible
from .lifecycle import EnvironmentLifecycle
from .models import EnvironmentProviderRecord, EnvironmentRecord

logger = get_logger(__name__)


class EnvironmentMaintenanceLoop:
    def __init__(self, lifecycle: EnvironmentLifecycle, *, interval_seconds: float = 5, concurrency: int = 8) -> None:
        self.lifecycle = lifecycle
        self.interval_seconds = interval_seconds
        self.concurrency = concurrency
        self._draining = asyncio.Event()
        self._stopped = asyncio.Event()

    def drain(self) -> None:
        self._draining.set()

    def is_draining(self) -> bool:
        return self._draining.is_set()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def run_once(self) -> None:
        now = self.lifecycle.clock()
        async with transaction(self.lifecycle.sessions) as session:
            ids = tuple(
                await session.scalars(
                    select(EnvironmentRecord.id)
                    .join(EnvironmentProviderRecord, EnvironmentProviderRecord.id == EnvironmentRecord.provider_id)
                    .where(
                        EnvironmentRecord.ownership == "managed",
                        local_backend_eligible(),
                        or_(EnvironmentRecord.status != "deleted", EnvironmentRecord.operation_id.is_not(None)),
                        EnvironmentRecord.next_maintenance_at <= now,
                    )
                    .order_by(EnvironmentRecord.next_maintenance_at, EnvironmentRecord.id)
                    .limit(self.concurrency)
                )
            )

            if ids:
                await session.execute(
                    update(EnvironmentRecord)
                    .where(EnvironmentRecord.id.in_(ids))
                    .values(next_maintenance_at=now + timedelta(seconds=self.interval_seconds))
                )

        async def maintain(environment_id: str) -> None:
            try:
                await self.lifecycle.maintain(environment_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Environment maintenance failed", extra={"environment_id": environment_id})

        await asyncio.gather(*(maintain(environment_id) for environment_id in ids))

    async def run(self) -> None:
        try:
            while not self._draining.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(self._draining.wait(), self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            self._stopped.set()
