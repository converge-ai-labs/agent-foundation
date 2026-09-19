"""Worker-scoped batching of independently supervised Attempt renewals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_logging import get_logger
from anyio import Event, create_task_group, fail_after, sleep

from .attempts import AttemptAuthorityError, AttemptContext, AttemptExecutionService, AttemptMutationReceipt

logger = get_logger(__name__)


@dataclass(slots=True)
class _Renewal:
    authority: AttemptContext
    done: Event = field(default_factory=Event)
    receipt: AttemptMutationReceipt | None = None
    error: Exception | None = None
    cancelled: bool = False


class LeaseRenewalBatcher:
    """Coalesce monitor requests without granting authority or owning executors.

    The Worker opens this scope outside all executor roots, keeping it alive
    through drain and cleanup. Each monitor still owns its timeout and fencing.
    """

    def __init__(self, execution: AttemptExecutionService) -> None:
        self._execution = execution
        self._pending: list[_Renewal] = []
        self._wake = Event()
        self._running = False

    @asynccontextmanager
    async def open(self) -> AsyncIterator[None]:
        if self._running:
            raise RuntimeError("Worker lease renewal is already running")
        self._running = True
        try:
            async with create_task_group() as tasks:
                tasks.start_soon(self._run)
                try:
                    yield
                finally:
                    self._running = False
                    tasks.cancel_scope.cancel()
        finally:
            self._running = False
            self._reject_pending()

    async def renew(self, authority: AttemptContext) -> AttemptMutationReceipt:
        if not self._running:
            raise AttemptAuthorityError("Worker lease renewal is unavailable")
        request = _Renewal(authority)
        self._pending.append(request)
        self._wake.set()
        try:
            await request.done.wait()
            if request.receipt is None:
                raise AttemptAuthorityError("Attempt batch renewal could not confirm authority") from request.error
            return request.receipt
        finally:
            # A timed-out executor must never receive a late local lease extension.
            request.cancelled = True

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            # Monitors use one cadence. Allow a bounded scheduling window for
            # siblings to acquire their independent authority locks and enqueue.
            timeout = min(item.authority.renewal_timeout.total_seconds() for item in self._pending)
            await sleep(min(0.01, timeout / 10))
            pending, self._pending = self._pending, []
            self._wake = Event()
            try:
                for offset in range(0, len(pending), 128):
                    batch = [item for item in pending[offset : offset + 128] if not item.cancelled]
                    if batch:
                        await self._flush(batch)
            finally:
                # Includes shutdown cancellation during SQL; an unconfirmed
                # commit never becomes a successful receipt in process memory.
                for item in pending:
                    item.done.set()

    async def _flush(self, batch: list[_Renewal]) -> None:
        try:
            with fail_after(min(item.authority.renewal_timeout.total_seconds() for item in batch)):
                receipts = await self._execution.heartbeat_many([item.authority for item in batch])
        except Exception as error:
            logger.warning(
                "attempt_lease_batch_unconfirmed",
                extra={"attempt_count": len(batch), "error_type": type(error).__name__},
            )
            for item in batch:
                item.error = error
        else:
            for item in batch:
                if not item.cancelled:
                    item.receipt = receipts.get(item.authority.run_attempt_id)
            logger.debug(
                "attempt_lease_batch_renewed",
                extra={"attempt_count": len(batch), "renewed_count": len(receipts)},
            )
        finally:
            for item in batch:
                item.done.set()

    def _reject_pending(self) -> None:
        for item in self._pending:
            item.done.set()
        self._pending.clear()
        self._wake = Event()
