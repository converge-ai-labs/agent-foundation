"""Bounded Control recovery of retained client target observations."""

from __future__ import annotations

import asyncio

from a13n_logging import get_logger

from a13n_service.background import PeriodicLoop
from a13n_service.ids import new_object_id
from a13n_service.storage import is_database_unavailable

from ..errors import EnvironmentManagementError
from .authority import LeaseDeadline
from .resources import ConnectionTarget
from .service import ClientConnectionService

logger = get_logger(__name__)


class ClientConnectionReconciler(PeriodicLoop):
    def __init__(
        self,
        service: ClientConnectionService,
        *,
        interval_seconds: float = 5,
        batch_size: int = 100,
        concurrency: int = 8,
    ) -> None:
        if not 1 <= batch_size <= 1000 or not 1 <= concurrency <= batch_size:
            raise ValueError("Connection reconciliation requires bounded batches and concurrency")
        super().__init__(self._run_iteration, interval_seconds=interval_seconds)
        self._service = service
        self._batch_size = batch_size
        self._concurrency = concurrency
        self._after = ""

    async def _run_iteration(self) -> None:
        try:
            await self.run_once()
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            logger.warning("client_environment_reconciliation_database_unavailable")

    async def run_once(self) -> None:
        targets = await self._service.resources.reconciliation_batch(after_id=self._after, limit=self._batch_size)
        if not targets and self._after:
            self._after = ""
            targets = await self._service.resources.reconciliation_batch(limit=self._batch_size)
        if not targets:
            return
        semaphore = asyncio.Semaphore(self._concurrency)

        async def visit(target: ConnectionTarget) -> None:
            async with semaphore:
                if not self._draining.is_set():
                    await self._visit(target)

        async with asyncio.TaskGroup() as tasks:
            for target in targets:
                tasks.create_task(visit(target))
        self._after = targets[-1].environment_id

    async def _visit(self, target: ConnectionTarget) -> None:
        try:
            observation = await self._service.observe(target.organization_id, target.environment_id)
            if observation.value.status == "connecting" and target.provider_enabled:
                return
            status = "running" if observation.value.status == "online" and target.provider_enabled else "unavailable"
            if status == target.status and target.operation_id is None:
                return
            deadline = observation.request_started_at + 1
            if observation.value.status != "offline":
                deadline = min(deadline, observation.deadline().monotonic_at)
            published = await self._service.resources.publish(
                target,
                status,
                publication_id=new_object_id("aud"),
                evidence_deadline=LeaseDeadline(deadline),
            )
            if published:
                logger.info(
                    "client_environment_observation_reconciled",
                    extra={"environment_id": target.environment_id, "status": status},
                )
        except EnvironmentManagementError as error:
            if error.code != "environment_coordination_unavailable":
                raise
            logger.warning(
                "client_environment_reconciliation_coordination_unavailable",
                extra={"environment_id": target.environment_id},
            )
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            logger.warning(
                "client_environment_reconciliation_database_unavailable",
                extra={"environment_id": target.environment_id},
            )
