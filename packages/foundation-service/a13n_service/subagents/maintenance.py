"""Process-lifetime reconciliation of durable child cancellation and result delivery."""

from anyio import Event, move_on_after

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
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("subagent reconciliation interval must be positive")
        self._cancellation = cancellation
        self._results = results
        self._successors = successors
        self._interval = poll_interval_seconds
        self._draining = Event()
        self._stopped = Event()

    async def reconcile_once(self) -> None:
        await self._cancellation.reconcile_once()
        await self._results.reconcile_once()
        await self._successors.reconcile_once()

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
