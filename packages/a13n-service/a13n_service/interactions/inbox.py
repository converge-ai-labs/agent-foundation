"""Durable Thread-inbox commands, reconciliation, and wake signals."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from a13n_harness import RunInputValue
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .attempts import (
    AttemptContext,
    AttemptMutationReceipt,
    lock_attempt_authority,
    read_attempt_lease,
)
from .control_domain import (
    SteerReceipt,
    SteerStatus,
    ThreadInboxEntry,
    ThreadInboxKind,
    ThreadInboxStatus,
    new_thread_inbox_entry_id,
)
from .control_models import ThreadInboxRecord
from .inbox_allocation import allocate_steer
from .inbox_delivery import AdaptedThreadInboxEntry
from .inbox_persistence import ThreadInboxConflict, reconcile_checkpoint
from .input import AcceptedAgentInput
from .models import RunRecord, ThreadRecord
from .objects import StoredRunState
from .state import ConsumedThreadInboxEntry

logger = logging.getLogger("a13n_service.interactions.inbox")


class InboxPayloadMaterializer(Protocol):
    async def __call__(self, entry: ThreadInboxEntry) -> RunInputValue: ...


class ThreadControlSignalPublisher(Protocol):
    async def publish(self, *, organization_id: str, thread_id: str) -> None: ...


class ThreadInboxStore:
    """Persist accepted steer commands and expose their safe status projection."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        signals: ThreadControlSignalPublisher | None = None,
        max_pending_count: int = 256,
        max_pending_bytes: int = 8 * 1024 * 1024,
        clock: Clock = utc_now,
    ) -> None:
        if max_pending_count < 1 or max_pending_bytes < 1:
            raise ValueError("Thread inbox admission limits must be positive")
        self._sessions = sessions
        self._signals = signals
        self._max_pending_count = max_pending_count
        self._max_pending_bytes = max_pending_bytes
        self._clock = clock

    async def append_steer(
        self,
        *,
        organization_id: str,
        run_id: str,
        input: AcceptedAgentInput,
        entry_id: str | None = None,
        final_validator: Callable[[AsyncSession], Awaitable[None]] | None = None,
        transaction_hook: Callable[[AsyncSession, SteerReceipt], Awaitable[None]] | None = None,
    ) -> SteerReceipt:
        """Append one already-authorized, canonical steer to the current Run."""

        now = assume_utc(self._clock())
        steer_id = entry_id or new_thread_inbox_entry_id()
        payload = input.model_dump(mode="json", by_alias=True, exclude_none=True)
        async with transaction(self._sessions) as database:
            thread, run = await _lock_current_run(database, organization_id=organization_id, run_id=run_id)
            if final_validator is not None:
                await final_validator(database)
            target_run_id: str | None
            source_waiting_run_id: str | None
            if run.status in {"accepted", "running"}:
                target_run_id = run.id
                source_waiting_run_id = None
            elif run.status == "waiting" and thread.head_run_id == run.id:
                target_run_id = None
                source_waiting_run_id = run.id
            else:
                raise ThreadInboxConflict("steer target is not the current accepted, running, or selected waiting Run")
            entry = await allocate_steer(
                database,
                organization_id=organization_id,
                thread_id=thread.id,
                accepted_against_run_id=run.id,
                target_run_id=target_run_id,
                source_waiting_run_id=source_waiting_run_id,
                entry_id=steer_id,
                payload=payload,
                payload_size_bytes=len(input.canonical_bytes()),
                max_pending_count=self._max_pending_count,
                max_pending_bytes=self._max_pending_bytes,
                now=now,
            )
            receipt = SteerReceipt(
                session_id=thread.session_id,
                thread_id=thread.id,
                run_id=run.id,
                steer_id=entry.id,
                delivery_sequence=entry.delivery_sequence,
                accepted_at=now,
            )
            if transaction_hook is not None:
                await transaction_hook(database, receipt)
        await self._best_effort_signal(organization_id=organization_id, thread_id=receipt.thread_id)
        return receipt

    async def get_steer(self, *, organization_id: str, run_id: str, steer_id: str) -> SteerStatus:
        async with short_session(self._sessions) as database:
            row = await database.scalar(
                select(ThreadInboxRecord).where(
                    ThreadInboxRecord.organization_id == organization_id,
                    ThreadInboxRecord.id == steer_id,
                    ThreadInboxRecord.kind == ThreadInboxKind.steer.value,
                    ThreadInboxRecord.accepted_against_run_id == run_id,
                )
            )
            if row is None:
                raise ThreadInboxConflict("steer receipt was not found")
            thread = await database.scalar(
                select(ThreadRecord).where(
                    ThreadRecord.organization_id == organization_id,
                    ThreadRecord.id == row.thread_id,
                )
            )
            if thread is None:
                raise ThreadInboxConflict("steer Thread was not found")
            entry = row.to_resource()
            if entry.status not in {
                ThreadInboxStatus.pending,
                ThreadInboxStatus.consumed,
                ThreadInboxStatus.superseded,
            }:
                raise ThreadInboxConflict("steer has an invalid public disposition")
            if entry.status is ThreadInboxStatus.pending:
                public_status = ThreadInboxStatus.pending
            elif entry.status is ThreadInboxStatus.consumed:
                public_status = ThreadInboxStatus.consumed
            else:
                public_status = ThreadInboxStatus.superseded
            return SteerStatus(
                session_id=thread.session_id,
                thread_id=entry.thread_id,
                accepted_against_run_id=run_id,
                steer_id=entry.id,
                delivery_sequence=entry.delivery_sequence,
                target_run_id=entry.target_run_id,
                source_waiting_run_id=entry.source_waiting_run_id,
                status=public_status,
                consumed_by_run_id=entry.consumed_by_run_id,
                consumed_state_digest_sha256=entry.consumed_state_digest_sha256,
                consumed_checkpoint_seq=entry.consumed_checkpoint_seq,
                created_at=entry.created_at,
                finalized_at=entry.finalized_at,
            )

    async def _best_effort_signal(self, *, organization_id: str, thread_id: str) -> None:
        if self._signals is None:
            return
        try:
            await self._signals.publish(organization_id=organization_id, thread_id=thread_id)
        except Exception:
            logger.warning(
                "thread_control_signal_failed",
                extra={"event": "thread_control_signal_failed", "thread_id": thread_id},
                exc_info=True,
            )


class DatabaseThreadInboxReconciler:
    """Bridge durable Thread-inbox authority into one fenced Harness execution."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        materialize: InboxPayloadMaterializer,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._materialize = materialize
        self._clock = clock

    async def confirm_checkpoint(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            run, attempt, _ = await lock_attempt_authority(
                database,
                authority,
                now,
                lock_inbox_origins=True,
            )
            await reconcile_checkpoint(database, run=run, state=state, now=now)
            return AttemptMutationReceipt(
                run_version=run.version,
                attempt_version=attempt.version,
                lease_expires_at=assume_utc(attempt.lease_expires_at),
            )

    async def read_eligible(
        self,
        authority: AttemptContext,
    ) -> Sequence[AdaptedThreadInboxEntry]:
        now = assume_utc(self._clock())
        async with short_session(self._sessions) as database:
            run, _, _ = await read_attempt_lease(database, authority, now)
            pending = tuple(
                (
                    await database.scalars(
                        select(ThreadInboxRecord)
                        .where(
                            ThreadInboxRecord.organization_id == run.organization_id,
                            ThreadInboxRecord.thread_id == run.thread_id,
                            ThreadInboxRecord.status == ThreadInboxStatus.pending.value,
                        )
                        .order_by(ThreadInboxRecord.delivery_sequence, ThreadInboxRecord.id)
                    )
                ).all()
            )
            rows = _contiguous_target_prefix(pending, run_id=run.id)
            entries = tuple(row.to_resource() for row in rows)

        adapted: list[AdaptedThreadInboxEntry] = []
        for entry in entries:
            value = await self._materialize(entry)
            adapted.append(
                AdaptedThreadInboxEntry(
                    delivery_sequence=entry.delivery_sequence,
                    receipt=ConsumedThreadInboxEntry(
                        inbox_entry_id=entry.id,
                        kind=entry.kind.value,
                    ),
                    input=value,
                )
            )
        return tuple(adapted)


class RedisThreadControlSignals:
    """Loss-tolerant wake signals for Thread reconciliation."""

    def __init__(
        self,
        redis: Redis,
        *,
        max_length: int = 256,
        ttl_seconds: int = 3600,
        group: str = "reconcile",
    ) -> None:
        if max_length < 1 or ttl_seconds < 1 or not group:
            raise ValueError("Thread control signal bounds must be positive")
        self._redis = redis
        self._max_length = max_length
        self._ttl_seconds = ttl_seconds
        self._group = group

    async def publish(self, *, organization_id: str, thread_id: str) -> None:
        key = _signal_key(organization_id, thread_id)
        await self._ensure_group(key)
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.xadd(
            key,
            {b"schema_version": b"1", b"thread_id": thread_id.encode("utf-8")},
            maxlen=self._max_length,
            approximate=True,
        )
        pipeline.expire(key, self._ttl_seconds)
        await pipeline.execute()

    async def read_new(
        self,
        *,
        organization_id: str,
        thread_id: str,
        consumer: str,
        count: int = 16,
        block_ms: int | None = None,
    ) -> tuple[ThreadControlSignal, ...]:
        """Read new wakeups; callers reconcile PostgreSQL before acknowledging."""

        _validate_signal_read(consumer=consumer, count=count, block_ms=block_ms)
        key = _signal_key(organization_id, thread_id)
        await self._ensure_group(key)
        streams = await self._redis.xreadgroup(
            self._group,
            consumer,
            {key: b">"},
            count=count,
            block=block_ms,
        )
        await self._redis.expire(key, self._ttl_seconds)
        return _decode_signals(streams, expected_key=key, thread_id=thread_id)

    async def claim_abandoned(
        self,
        *,
        organization_id: str,
        thread_id: str,
        consumer: str,
        min_idle_ms: int,
        count: int = 16,
    ) -> tuple[ThreadControlSignal, ...]:
        """Claim bounded wakeups left pending by a prior Worker generation."""

        _validate_signal_read(consumer=consumer, count=count, block_ms=min_idle_ms)
        key = _signal_key(organization_id, thread_id)
        await self._ensure_group(key)
        claimed = await self._redis.xautoclaim(
            key,
            self._group,
            consumer,
            min_idle_ms,
            start_id=b"0-0",
            count=count,
        )
        await self._redis.expire(key, self._ttl_seconds)
        entries = claimed[1]
        return tuple(ThreadControlSignal(stream_id=_as_bytes(entry_id), thread_id=thread_id) for entry_id, _ in entries)

    async def acknowledge(
        self,
        *,
        organization_id: str,
        thread_id: str,
        stream_ids: Sequence[bytes],
    ) -> int:
        """Acknowledge wakeups only after the caller's reconciliation attempt."""

        if not stream_ids:
            return 0
        key = _signal_key(organization_id, thread_id)
        acknowledged = await self._redis.xack(key, self._group, *stream_ids)
        await self._redis.expire(key, self._ttl_seconds)
        return int(acknowledged)

    async def _ensure_group(self, key: bytes) -> None:
        try:
            await self._redis.xgroup_create(key, self._group, id=b"0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise


@dataclass(frozen=True, slots=True)
class ThreadControlSignal:
    stream_id: bytes
    thread_id: str


async def _lock_current_run(
    database: AsyncSession,
    *,
    organization_id: str,
    run_id: str,
) -> tuple[ThreadRecord, RunRecord]:
    thread_id = await database.scalar(
        select(RunRecord.thread_id).where(RunRecord.organization_id == organization_id, RunRecord.id == run_id)
    )
    if thread_id is None:
        raise ThreadInboxConflict("steer target Run was not found")
    thread = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.organization_id == organization_id, ThreadRecord.id == thread_id)
        .with_for_update()
    )
    run = await database.scalar(
        select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id == run_id).with_for_update()
    )
    if thread is None or run is None or thread.current_run_id != run.id:
        raise ThreadInboxConflict("steer target is not the current Run")
    return thread, run


def _signal_key(organization_id: str, thread_id: str) -> bytes:
    return f"a13n:control:{organization_id}:{thread_id}".encode()


def _validate_signal_read(*, consumer: str, count: int, block_ms: int | None) -> None:
    if not consumer or count < 1 or count > 1024:
        raise ValueError("control signal consumer and count are invalid")
    if block_ms is not None and block_ms < 0:
        raise ValueError("control signal block or idle duration cannot be negative")


def _decode_signals(
    streams: object,
    *,
    expected_key: bytes,
    thread_id: str,
) -> tuple[ThreadControlSignal, ...]:
    if isinstance(streams, Mapping):
        stream_items = tuple(streams.items())
    elif isinstance(streams, Sequence) and not isinstance(streams, bytes | str):
        stream_items = tuple(cast(Sequence[tuple[bytes | str, Sequence[object]]], streams))
    else:
        raise RuntimeError("Redis returned an invalid Thread control signal response")
    result: list[ThreadControlSignal] = []
    for key, entries in stream_items:
        if _as_bytes(key) != expected_key:
            raise RuntimeError("Redis returned a Thread control signal from another Stream")
        for entry in entries:
            entry_id, _ = cast(tuple[bytes | str, object], entry)
            result.append(ThreadControlSignal(stream_id=_as_bytes(entry_id), thread_id=thread_id))
    return tuple(result)


def _as_bytes(value: bytes | str) -> bytes:
    return value if isinstance(value, bytes) else value.encode("utf-8")


def _contiguous_target_prefix(
    rows: Sequence[ThreadInboxRecord],
    *,
    run_id: str,
) -> tuple[ThreadInboxRecord, ...]:
    selected: list[ThreadInboxRecord] = []
    for row in rows:
        if row.target_run_id != run_id:
            break
        selected.append(row)
    return tuple(selected)


__all__ = [
    "DatabaseThreadInboxReconciler",
    "InboxPayloadMaterializer",
    "RedisThreadControlSignals",
    "ThreadControlSignal",
    "ThreadControlSignalPublisher",
    "ThreadInboxStore",
]
