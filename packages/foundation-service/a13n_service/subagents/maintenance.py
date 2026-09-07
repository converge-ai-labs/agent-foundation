"""Process-lifetime reconciliation of durable child cancellation and result delivery."""

from functools import partial

from anyio import Event, move_on_after

from a13n_service.background import PeriodicTask, Sweep

from .cancellation import ChildCancellationReconciler
from .results import AsyncSubagentResultPublisher
from .successors import AsyncSubagentSuccessorReconciler


class SubagentMaintenance:
    def __init__(
        self,
        cancellation: ChildCancellationReconciler,
        results: AsyncSubagentResultPublisher,
        successors: AsyncSubagentSuccessorReconciler,
        *,
        poll_interval_seconds: float,
        batch_limit: int = 64,
        item_timeout_seconds: float = 30,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("subagent reconciliation interval must be positive")
        self._cancellation = cancellation
        self._interval = poll_interval_seconds
        self._tasks = tuple(
            PeriodicTask(
                name,
                scan,
                interval_seconds=poll_interval_seconds,
                timeout_seconds=(item_timeout_seconds + 1) * batch_limit,
            )
            for name, scan in (
                ("subagent_cancellation", self._cancel_children),
                (
                    "async_result_publication",
                    partial(results.scan, limit=batch_limit, item_timeout_seconds=item_timeout_seconds),
                ),
                (
                    "async_result_successor",
                    partial(successors.scan, limit=batch_limit, item_timeout_seconds=item_timeout_seconds),
                ),
            )
        )
        self._draining = Event()
        self._stopped = Event()

    async def reconcile_once(self) -> None:
        for task in self._tasks:
            await task.run_once()

    async def _cancel_children(self) -> Sweep:
        completed = await self._cancellation.reconcile_once()
        return Sweep(completed=completed)

    def drain(self) -> None:
        self._draining.set()

    def is_draining(self) -> bool:
        return self._draining.is_set()

    async def wait_stopped(self) -> None:
        await self._stopped.wait()

    async def run(self) -> None:
        try:
            while not self.is_draining():
                await self.reconcile_once()
                with move_on_after(self._interval):
                    await self._draining.wait()
        finally:
            self._stopped.set()
