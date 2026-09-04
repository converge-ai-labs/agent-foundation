"""Worker-owned lifecycle maintenance, independent of Plugin Runtime locks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from a13n_logging import get_logger
from sqlalchemy import select

from a13n_service.storage import short_session

from .lifecycle import EnvironmentLifecycle
from .models import EnvironmentRecord

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
        async with short_session(self.lifecycle.sessions) as session:
            ids = tuple(
                await session.scalars(
                    select(EnvironmentRecord.id)
                    .where(
                        EnvironmentRecord.ownership == "managed",
                        EnvironmentRecord.next_maintenance_at <= datetime.now(UTC),
                    )
                    .order_by(EnvironmentRecord.next_maintenance_at)
                    .limit(self.concurrency)
                )
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
