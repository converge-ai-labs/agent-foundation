"""Process-owned, crash-recoverable consumption of Run display observations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

import anyio

from a13n_service.storage import ObjectConflict, ObjectNotFound
from a13n_service.storage.codec import canonical_model_bytes, run_codec
from a13n_service.temporal import utc_now

from .display_candidates import DisplayCandidate, DisplayCandidates, DisplayLane
from .display_model import DisplayLimitExceeded, RunDisplaySnapshot
from .display_projection import project_display
from .display_store import RunDisplayStore, StoredDisplay
from .domain import PublicationContinuityLost, RunStreamReplayGap
from .recovery import display_recovery_deadline
from .redis import RedisRunStream, run_stream_key_digest_sha256

logger = logging.getLogger("a13n_service.run_stream.display_consumer")
# Reserve closure time, a bounded safe reason, and version growth so reaching the
# content cap never prevents recording incomplete finalization of the last prefix.
_FINALIZATION_RESERVE_BYTES = 512


@dataclass(frozen=True, slots=True)
class DisplayConsumerPolicy:
    concurrency: int = 4
    candidate_batch_size: int = 32
    event_batch_size: int = 64
    flush_events: int = 256
    flush_bytes: int = 1024 * 1024
    flush_interval_seconds: float = 1
    poll_interval_seconds: float = 0.25
    operation_timeout_seconds: float = 10
    max_items: int = 2048
    max_snapshot_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            min(
                self.concurrency,
                self.candidate_batch_size,
                self.event_batch_size,
                self.flush_events,
                self.flush_bytes,
                self.flush_interval_seconds,
                self.poll_interval_seconds,
                self.operation_timeout_seconds,
                self.max_items,
                self.max_snapshot_bytes,
            )
            <= 0
            or self.event_batch_size > 1000
            or self.max_snapshot_bytes < 1024
        ):
            raise ValueError(
                "display bounds must be positive, snapshots at least 1024 bytes, and batches at most 1000 events"
            )


_DEFAULT_POLICY = DisplayConsumerPolicy()


@dataclass(frozen=True, slots=True)
class _DisplayBatch:
    snapshot: RunDisplaySnapshot
    events: int
    source_bytes: int
    pending_events: int
    pending_bytes: int
    lag_seconds: float


class RunDisplayConsumer:
    def __init__(
        self,
        candidates: DisplayCandidates,
        stream: RedisRunStream,
        store: RunDisplayStore,
        *,
        policy: DisplayConsumerPolicy = _DEFAULT_POLICY,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._candidates = candidates
        self._stream = stream
        self._store = store
        self._policy = policy
        self._clock = clock
        self._after: dict[DisplayLane, DisplayCandidate | None] = {}
        self._draining = False
        self._stopped = anyio.Event()

    def is_draining(self) -> bool:
        return self._draining

    async def shutdown(self) -> None:
        self._draining = True
        # In-flight work flushes its current bounded batch. Cancellation later
        # discards only speculative state; the unpersisted Redis suffix survives.
        with anyio.move_on_after(self._policy.operation_timeout_seconds):
            await self._stopped.wait()

    async def run(self) -> None:
        try:
            async with anyio.create_task_group() as tasks:
                for lane in ("active", "recovery", "cleanup"):
                    tasks.start_soon(self._run_lane, lane)
        finally:
            self._stopped.set()

    async def _run_lane(self, lane: DisplayLane) -> None:
        while not self._draining:
            try:
                await self.consume_once(lane=lane)
            except Exception:
                logger.exception(
                    "Display candidate discovery failed", extra={"event": "display_discovery_failed", "lane": lane}
                )
            await anyio.sleep(self._policy.poll_interval_seconds)

    async def consume_once(self, *, lane: DisplayLane = "active") -> int:
        candidates = await self._candidates.page(
            lane=lane, after=self._after.get(lane), limit=self._policy.candidate_batch_size, now=self._clock()
        )
        self._after[lane] = candidates[-1] if candidates else None
        # Recovery and cleanup each have one independent slot; slow archival
        # cannot occupy the active lane's concurrency budget.
        semaphore = anyio.Semaphore(self._policy.concurrency if lane == "active" else 1)
        async with anyio.create_task_group() as tasks:
            for candidate in candidates:
                tasks.start_soon(self._consume_candidate, candidate, semaphore)
        return len(candidates)

    async def _consume_candidate(self, candidate: DisplayCandidate, semaphore: anyio.Semaphore) -> None:
        async with semaphore:
            if self._draining:
                return
            try:
                with anyio.fail_after(self._policy.operation_timeout_seconds):
                    await self.consume_run(candidate)
            except ObjectConflict:
                # The next visit loads the winning cursor and open accumulators.
                logger.info(
                    "Display publication superseded", extra={"event": "display_conflict", "run_id": candidate.run_id}
                )
            except Exception:
                logger.exception(
                    "Display consumption failed", extra={"event": "display_flush_failed", "run_id": candidate.run_id}
                )
                if candidate.sealed_at is not None:
                    with anyio.fail_after(self._policy.operation_timeout_seconds):
                        await self._candidates.retry_after(candidate, when=self._clock() + timedelta(seconds=5))

    async def consume_run(self, candidate: DisplayCandidate) -> StoredDisplay | None:
        deadline = display_recovery_deadline(candidate.sealed_at)
        if deadline is not None:
            # Set expiry before touching object storage, including during outages.
            await self._stream.schedule_expiry(candidate.organization_id, candidate.run_id, deadline=deadline)
            if self._clock() >= deadline:
                await self._candidates.settle(candidate, now=self._clock())
                return None
            with anyio.fail_after((deadline - self._clock()).total_seconds()):
                return await self._consume_run(candidate)
        return await self._consume_run(candidate)

    async def _consume_run(self, candidate: DisplayCandidate) -> StoredDisplay | None:
        started = monotonic()
        try:
            previous = await self._store.read(
                candidate.organization_id, candidate.run_id, expected_thread_id=candidate.thread_id
            )
        except ObjectNotFound:
            previous = None
        base = (
            previous.snapshot
            if previous is not None
            else RunDisplaySnapshot(
                version=1,
                run_id=candidate.run_id,
                thread_id=candidate.thread_id,
                cursor=None,
                stream_key_digest_sha256=run_stream_key_digest_sha256(candidate.organization_id, candidate.run_id),
            )
        )
        if base.finalized:
            try:
                await self._acknowledge(candidate, base)
            except PublicationContinuityLost:
                pass  # Its acknowledged Redis horizon may already have expired.
            await self._candidates.settle(candidate, now=self._clock())
            return previous
        if previous is not None and base.complete and base.cursor is not None:
            # Restore the Redis watermark after a crash between PUT and ACK.
            try:
                await self._acknowledge(candidate, base)
            except PublicationContinuityLost:
                pass  # The contiguous read below records the lost source explicitly.
        batch = await self._consume_batch(candidate, base, started=started)
        snapshot = batch.snapshot
        if snapshot == base:
            return previous
        snapshot = snapshot.model_copy(update={"version": 1 if previous is None else previous.snapshot.version + 1})
        stored = await self._store.publish(candidate.organization_id, snapshot, previous=previous)
        await self._acknowledge(candidate, stored.snapshot)
        if stored.snapshot.finalized:
            await self._candidates.settle(candidate, now=self._clock())
        logger.info(
            "Run display checkpoint persisted",
            extra={
                "event": "display_flushed",
                "run_id": candidate.run_id,
                "version": snapshot.version,
                "cursor": snapshot.cursor,
                "source_events": batch.events,
                "source_bytes": batch.source_bytes,
                "pending_events": batch.pending_events,
                "pending_bytes": batch.pending_bytes,
                "consumption_lag_seconds": batch.lag_seconds,
                "flush_seconds": monotonic() - started,
                "complete": snapshot.complete,
                "finalized": snapshot.finalized,
            },
        )
        return stored

    async def _consume_batch(
        self, candidate: DisplayCandidate, base: RunDisplaySnapshot, *, started: float
    ) -> _DisplayBatch:
        snapshot = base
        events = 0
        source_bytes = 0
        pending_events = pending_bytes = 0
        lag_seconds = 0.0
        reason = base.incomplete_reason
        closed_at = None
        try:
            while reason is None:
                page = await self._stream.read_for_display(
                    candidate.organization_id,
                    candidate.run_id,
                    after_stream_id=snapshot.cursor,
                    limit=min(self._policy.event_batch_size, self._policy.flush_events - events),
                )
                pending_events, pending_bytes = page.pending_events, page.pending_bytes
                if page.items:
                    lag_seconds = max(lag_seconds, (utc_now() - page.items[0].event.occurred_at).total_seconds())
                    snapshot = project_display(snapshot, page.items, max_items=self._policy.max_items)
                    if (
                        len(await run_codec(canonical_model_bytes, snapshot)) + _FINALIZATION_RESERVE_BYTES
                        > self._policy.max_snapshot_bytes
                    ):
                        raise DisplayLimitExceeded("display bytes exceed their configured bound")
                    events += len(page.items)
                    source_bytes += sum(len(canonical_model_bytes(entry.event)) for entry in page.items)
                at_tail = snapshot.cursor == page.high_watermark
                if page.closed and at_tail:
                    settlement = await self._candidates.settlement(candidate)
                    if settlement.closed_at is not None:
                        closed_at = settlement.closed_at
                        if settlement.lifecycle_missing:
                            reason = "lifecycle_history_unavailable"
                        elif settlement.abandoned:
                            reason = "lifecycle_projection_abandoned"
                    break
                if not page.items:
                    if snapshot.cursor is None:
                        settlement = await self._candidates.settlement(candidate)
                        if settlement.accepted_projected or settlement.closed_at is not None:
                            reason = "source_unavailable"
                    break
                if (
                    at_tail
                    or events >= self._policy.flush_events
                    or source_bytes >= self._policy.flush_bytes
                    or monotonic() - started >= self._policy.flush_interval_seconds
                    or self._draining
                ):
                    break
        except (RunStreamReplayGap, PublicationContinuityLost):
            reason = "source_discontinuity"
        except DisplayLimitExceeded:
            snapshot = base
            reason = "display_limit_exceeded"
        if reason is not None:
            # Never commit speculative progress from an iteration that encountered
            # a gap or limit: persisted Items and their cursor remain authoritative.
            snapshot = base
            settlement = await self._candidates.settlement(candidate)
            closed_at = settlement.closed_at
        if snapshot == base and reason == base.incomplete_reason and closed_at is None:
            return _DisplayBatch(base, events, source_bytes, pending_events, pending_bytes, lag_seconds)
        snapshot = project_display(snapshot, (), closed_at=closed_at, incomplete_reason=reason)
        return _DisplayBatch(snapshot, events, source_bytes, pending_events, pending_bytes, lag_seconds)

    async def _acknowledge(self, candidate: DisplayCandidate, snapshot: RunDisplaySnapshot) -> None:
        if not snapshot.complete:
            if snapshot.closed_at is not None:
                await self._stream.release_retired(
                    candidate.organization_id, candidate.run_id, closed_at=snapshot.closed_at
                )
            return
        if snapshot.cursor is not None:
            await self._stream.acknowledge_display(
                candidate.organization_id, candidate.run_id, cursor=snapshot.cursor, finalized=snapshot.finalized
            )
