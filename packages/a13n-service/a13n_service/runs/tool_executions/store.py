"""Shared execution facts and results. Admission precedes effects; completion commits its outbox intent."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
from dataclasses import asdict
from datetime import datetime, timedelta

from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionAdmissionRejected, ToolExecutionRecord
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import lock, now, short_session, transaction
from a13n_service.infra.objects.interface import ObjectRef, read
from a13n_service.infra.outbox import OutboxRow, enqueue
from a13n_service.runs.attempts import Lease, LeaseLost, lock_thread_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.tool_executions.payload import Payload

# Set by the worker's resource scope. The original Harness task inherits it; later readers need no origin.
ORIGIN: contextvars.ContextVar[Lease] = contextvars.ContextVar("service_tool_origin")
SEQUENTIAL: contextvars.ContextVar[bool] = contextvars.ContextVar("service_tool_sequential", default=False)


def available():
    return or_(ToolExecutionRow.expires_at.is_(None), ToolExecutionRow.expires_at > func.clock_timestamp())


def observation(row: ToolExecutionRow) -> ToolExecutionRecord:
    return ToolExecutionRecord.model_validate(row.record).model_copy(
        update={"delivery_acknowledged": row.first_delivered_at is not None}
    )


async def notify(session: AsyncSession, row: ToolExecutionRow) -> None:
    # Callers hold the execution row lock. Recovery and late foreground release
    # reuse the original intent without resetting a claimed or settled delivery.
    existing = await session.scalar(
        select(OutboxRow.id).where(OutboxRow.kind == "tool_result", OutboxRow.dedupe_key == row.id)
    )
    if existing is not None:
        return
    enqueue(
        session,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        kind="tool_result",
        dedupe_key=row.id,
        target={"thread_id": row.thread_id, "origin_run_id": row.run_id},
        payload={"execution_id": row.id},
    )


def delivered(row: ToolExecutionRow, at: datetime, seconds: int) -> None:
    if row.completed_at is not None and row.first_delivered_at is None:
        row.first_delivered_at = at
        assert row.expires_at is not None
        row.expires_at = min(row.expires_at, at + timedelta(seconds=seconds))


class Store:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def save(self, execution: StoredToolExecution) -> None:
        # Retry publication, never the operation. An uncertain commit is reconciled by stable identity.
        # Admission failures are returned immediately, before any effects can start.
        if not execution.record.terminal:
            await self._save(execution)
            return
        for attempt in range(3):
            try:
                await self._save(execution)
                return
            except LeaseLost:
                raise
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.1 * (2**attempt))

    async def _save(self, execution: StoredToolExecution) -> None:
        record, lease = execution.record, ORIGIN.get()
        if record.thread_id != lease.thread_id:
            raise LeaseLost()
        ref, key = None, None
        try:
            if record.terminal and (record.result_available or execution.retry_prompt is not None):
                async with short_session(self.runtime.storage) as session:
                    row = await session.get(ToolExecutionRow, record.execution_id)
                    if (
                        row is None
                        or row.worker_id != lease.worker_id
                        or not hmac.compare_digest(row.lease_token_hash, secret_hash(lease.token))
                    ):
                        raise LeaseLost()
                    upload = row.completed_at is None
                if upload:
                    data = Payload.encode(execution)
                    # Identical publication retries name identical immutable bytes.
                    key = (
                        f"tool-executions/{lease.workspace_id}/{record.execution_id}/{hashlib.sha256(data).hexdigest()}"
                    )
                    ref = await self.runtime.objects.put(key, data, content_type="application/json")
            await self._commit(record, lease, ref)
        finally:
            if key is not None:
                # Reconcile uncertain publication before removing bytes. A live owner may retry this key;
                # a terminal/expired identity can never publish a different result.
                async with short_session(self.runtime.storage) as session:
                    row = await session.get(ToolExecutionRow, record.execution_id)
                    discard = row is None or (
                        row.completed_at is not None and (row.result_ref is None or row.result_ref["key"] != key)
                    )
                if discard:
                    await self.runtime.objects.delete(key)

    async def _commit(self, record: ToolExecutionRecord, lease: Lease, ref: ObjectRef | None) -> None:
        async with transaction(self.runtime.storage) as session:
            row = await lock(session, ToolExecutionRow, record.execution_id)
            current = await now(session)
            if row is None:
                # Only initial admission may create a row. Expired identities cannot be resurrected.
                if record.status != "queued":
                    raise LeaseLost()
                # The INSERT's Thread foreign key also takes a row lock: follow Thread → Run → Attempt.
                try:
                    thread, run, _, current = await lock_thread_lease(session, lease)
                    if thread.archived_at is not None or run.cancel_requested_at is not None:
                        raise LeaseLost()
                except LeaseLost:
                    raise ToolExecutionAdmissionRejected(
                        "Tool admission was rejected: its origin is no longer current."
                    ) from None
                row = ToolExecutionRow(
                    id=record.execution_id,
                    organization_id=lease.organization_id,
                    workspace_id=lease.workspace_id,
                    thread_id=lease.thread_id,
                    run_id=lease.run_id,
                    attempt_id=lease.attempt_id,
                    worker_id=lease.worker_id,
                    lease_token_hash=secret_hash(lease.token),
                    lease_expires_at=current + timedelta(seconds=self.runtime.settings.worker.lease_seconds),
                    status=record.status,
                    sequential=SEQUENTIAL.get(),
                    created_at=record.created_at,
                    record=record.model_dump(mode="json"),
                    environment_mounts=run.environment_mounts,
                )
                session.add(row)
                return
            if row.worker_id != lease.worker_id or not hmac.compare_digest(
                row.lease_token_hash, secret_hash(lease.token)
            ):
                raise LeaseLost()
            if row.completed_at is not None:
                # A retry (or late foreground release) never changes the first terminal result or deadlines.
                previous = observation(row)
                if record.foreground_released and not previous.foreground_released:
                    row.record = previous.model_copy(update={"foreground_released": True}).model_dump(mode="json")
                    if row.first_delivered_at is None and row.expires_at is not None and row.expires_at > current:
                        await notify(session, row)
                return
            if row.lease_expires_at <= current:
                raise LeaseLost()
            if observation(row).foreground_released:
                record = record.model_copy(update={"foreground_released": True})
            row.status, row.record = record.status, record.model_dump(mode="json")
            if record.terminal:
                assert record.completed_at is not None
                row.completed_at = record.completed_at
                row.expires_at = record.completed_at + timedelta(
                    seconds=self.runtime.settings.tool_executions.max_retention_seconds
                )
                row.result_ref = asdict(ref) if ref is not None else None
                if record.foreground_released:
                    await notify(session, row)

    async def get(self, thread_id: str, execution_id: str) -> StoredToolExecution | None:
        async with short_session(self.runtime.storage) as session:
            row = await session.scalar(
                select(ToolExecutionRow).where(
                    ToolExecutionRow.id == execution_id, ToolExecutionRow.thread_id == thread_id, available()
                )
            )
            if row is None:
                return None
            record, ref = observation(row), ObjectRef(**row.result_ref) if row.result_ref is not None else None
        if ref is None:
            return StoredToolExecution(record)
        data = await read(self.runtime.objects, ref)
        return Payload.model_validate_json(data).execution(record)

    async def list(self, thread_id: str, *, offset: int, limit: int) -> tuple[ToolExecutionRecord, ...]:
        priority = case(
            (ToolExecutionRow.completed_at.is_(None), 0),
            ((ToolExecutionRow.status == "succeeded") & ToolExecutionRow.first_delivered_at.is_(None), 1),
            (ToolExecutionRow.status.in_(["failed", "cancelled", "lost"]), 2),
            else_=3,
        )
        async with short_session(self.runtime.storage) as session:
            rows = await session.scalars(
                select(ToolExecutionRow)
                .where(ToolExecutionRow.thread_id == thread_id, available())
                .order_by(priority, ToolExecutionRow.created_at.desc(), ToolExecutionRow.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(observation(row) for row in rows)

    async def acknowledge_delivery(self, thread_id: str, execution_id: str) -> None:
        async with transaction(self.runtime.storage) as session:
            row = await lock(session, ToolExecutionRow, execution_id)
            if row is not None and row.thread_id == thread_id:
                delivered(row, await now(session), self.runtime.settings.tool_executions.delivered_retention_seconds)
