"""Visit due Environments in bounded batches, independently of target operation leases."""

from __future__ import annotations

import asyncio
from datetime import datetime

from a13n_logging import get_logger
from sqlalchemy import or_, select, update

from a13n_service.storage import is_database_unavailable, short_session, transaction
from a13n_service.temporal import assume_utc

from .identity import local_backend_eligible
from .lifecycle import EnvironmentLifecycle
from .models import EnvironmentProviderRecord, EnvironmentRecord
from .policy import DEFAULT_BATCH_SIZE, FAILURE_BACKOFF

logger = get_logger(__name__)


class EnvironmentMaintenanceLoop:
    def __init__(
        self,
        lifecycle: EnvironmentLifecycle,
        *,
        interval_seconds: float = 5,
        concurrency: int = 4,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if interval_seconds <= 0 or concurrency < 1 or batch_size < 1:
            raise ValueError("Maintenance interval, concurrency, and batch size must be positive")
        self.lifecycle = lifecycle
        self.interval_seconds = interval_seconds
        self.concurrency = concurrency
        self.batch_size = batch_size
        self._draining = asyncio.Event()
        self._stopped = asyncio.Event()

    def drain(self) -> None:
        self._draining.set()

    def is_draining(self) -> bool:
        return self._draining.is_set()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def run_once(self) -> None:
        cutoff = assume_utc(self.lifecycle.clock())
        after = ""
        queue: asyncio.Queue[str | None] = asyncio.Queue(self.batch_size)

        async def visit() -> None:
            while (environment_id := await queue.get()) is not None:
                if not self._draining.is_set():
                    await self._visit(environment_id, cutoff)

        async with asyncio.TaskGroup() as tasks:
            for _ in range(self.concurrency):
                tasks.create_task(visit())
            while not self._draining.is_set():
                async with short_session(self.lifecycle.sessions) as session:
                    ids = tuple(
                        await session.scalars(
                            select(EnvironmentRecord.id)
                            .join(
                                EnvironmentProviderRecord, EnvironmentProviderRecord.id == EnvironmentRecord.provider_id
                            )
                            .where(
                                EnvironmentRecord.id > after,
                                EnvironmentRecord.ownership == "managed",
                                local_backend_eligible(),
                                or_(EnvironmentRecord.status != "deleted", EnvironmentRecord.operation_id.is_not(None)),
                                EnvironmentRecord.next_maintenance_at <= cutoff,
                            )
                            .order_by(EnvironmentRecord.id)
                            .limit(self.batch_size)
                        )
                    )
                if not ids:
                    break
                for environment_id in ids:
                    await queue.put(environment_id)
                after = ids[-1]
            for _ in range(self.concurrency):
                await queue.put(None)

    async def _visit(self, environment_id: str, cutoff: datetime) -> None:
        reserved_until = assume_utc(self.lifecycle.clock()) + self.lifecycle.lease_duration
        async with transaction(self.lifecycle.sessions) as session:
            row = await session.scalar(
                select(EnvironmentRecord)
                .where(EnvironmentRecord.id == environment_id, EnvironmentRecord.next_maintenance_at <= cutoff)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return
            row.next_maintenance_at = reserved_until
        try:
            await self.lifecycle.maintain(environment_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Environment maintenance failed", extra={"environment_id": environment_id})
            # Successful maintenance always installs its own deadline. Only a
            # failed visit without a pending operation needs a fallback retry.
            async with transaction(self.lifecycle.sessions) as session:
                await session.execute(
                    update(EnvironmentRecord)
                    .where(
                        EnvironmentRecord.id == environment_id,
                        EnvironmentRecord.next_maintenance_at == reserved_until,
                        EnvironmentRecord.operation_id.is_(None),
                    )
                    .values(next_maintenance_at=assume_utc(self.lifecycle.clock()) + FAILURE_BACKOFF)
                )

    async def run(self) -> None:
        try:
            while not self._draining.is_set():
                try:
                    await self.run_once()
                except Exception as error:
                    if not is_database_unavailable(error):
                        raise
                    logger.warning(
                        "environment_maintenance_database_unavailable", extra={"retry_seconds": self.interval_seconds}
                    )
                try:
                    await asyncio.wait_for(self._draining.wait(), self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            self._stopped.set()
