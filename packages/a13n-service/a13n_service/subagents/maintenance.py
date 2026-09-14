"""Process-lifetime reconciliation of durable child cancellation and result delivery."""

from functools import partial

from a13n_service.background import PeriodicLoop, PeriodicTask, Sweep

from .cancellation import ChildCancellationReconciler
from .results import AsyncSubagentResultPublisher
from .successors import AsyncSubagentSuccessorReconciler


class SubagentMaintenance(PeriodicLoop):
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
        super().__init__(self.reconcile_once, interval_seconds=poll_interval_seconds)
        self._cancellation = cancellation
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

    async def reconcile_once(self) -> None:
        for task in self._tasks:
            await task.run_once()

    async def _cancel_children(self) -> Sweep:
        completed = await self._cancellation.reconcile_once()
        return Sweep(completed=completed)
