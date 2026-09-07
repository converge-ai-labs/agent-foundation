"""Loss-tolerant Redis hints with periodic authoritative reconciliation."""

from __future__ import annotations

from anyio import fail_after, sleep
from redis.exceptions import RedisError

from .attempts import AttemptContext
from .inbox import RedisThreadControlSignals, ThreadControlSignal


class AttemptControlWakeups:
    def __init__(self, signals: RedisThreadControlSignals, context: AttemptContext) -> None:
        self._signals = signals
        self._context = context

    async def receive(self) -> object:
        context = self._context
        try:
            with fail_after(context.reconciliation_timeout.total_seconds()):
                abandoned = await self._signals.claim_abandoned(
                    organization_id=context.organization_id,
                    thread_id=context.thread_id,
                    consumer=context.run_attempt_id,
                    min_idle_ms=int(context.lease_duration.total_seconds() * 1000),
                )
                if abandoned:
                    return abandoned
                return await self._signals.read_new(
                    organization_id=context.organization_id,
                    thread_id=context.thread_id,
                    consumer=context.run_attempt_id,
                    block_ms=1000,
                )
        except (RedisError, TimeoutError):
            await sleep(1)
            return ()

    async def acknowledge(self, signal: object) -> None:
        if not isinstance(signal, tuple) or any(not isinstance(item, ThreadControlSignal) for item in signal):
            raise TypeError("Control wakeup batch is invalid")
        try:
            await self._signals.acknowledge(
                organization_id=self._context.organization_id,
                thread_id=self._context.thread_id,
                stream_ids=tuple(item.stream_id for item in signal),
            )
        except RedisError:
            # A lost acknowledgement is retried by abandoned-entry claiming.
            return
