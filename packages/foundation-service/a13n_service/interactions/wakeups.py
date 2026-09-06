"""Bounded Redis wakeups with PostgreSQL polling when hints are absent."""

from __future__ import annotations

from anyio import fail_after, sleep

from .attempts import AttemptContext
from .inbox import RedisThreadControlSignals, ThreadControlSignal


class ThreadControlWakeups:
    """Keep transport acknowledgements separate from durable inbox consumption."""

    def __init__(
        self,
        signals: RedisThreadControlSignals,
        context: AttemptContext,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Thread wakeup poll interval must be positive")
        self._signals = signals
        self._context = context
        self._poll = poll_interval_seconds
        self._consumer = f"{context.worker_id}:{context.worker_generation}:{context.run_attempt_id}"
        self._pending: tuple[ThreadControlSignal, ...] | None = None

    async def receive(self) -> object:
        if self._pending is not None:
            raise RuntimeError("Thread wakeups must be reconciled and acknowledged before reading again")
        context = self._context
        with fail_after(context.reconciliation_timeout.total_seconds()):
            signals = await self._signals.claim_abandoned(
                tenant_id=context.tenant_id,
                thread_id=context.thread_id,
                consumer=self._consumer,
                min_idle_ms=max(1, int(context.lease_duration.total_seconds() * 1000)),
                count=16,
            )
            if not signals:
                signals = await self._signals.read_new(
                    tenant_id=context.tenant_id,
                    thread_id=context.thread_id,
                    consumer=self._consumer,
                    count=16,
                )
        if not signals:
            await sleep(self._poll)
        self._pending = signals
        return signals

    async def acknowledge(self, signal: object) -> None:
        if self._pending is None or signal is not self._pending:
            raise ValueError("Thread acknowledgement does not match the delivered wakeup batch")
        with fail_after(self._context.reconciliation_timeout.total_seconds()):
            await self._signals.acknowledge(
                tenant_id=self._context.tenant_id,
                thread_id=self._context.thread_id,
                stream_ids=tuple(item.stream_id for item in self._pending),
            )
        self._pending = None
