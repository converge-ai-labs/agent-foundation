"""Detached compaction persistence: generation authority and checkpoint settlement.

An operation reserves its Thread before uploading input. Only its original owner
may generate; expired owners become failed observations, never queued work.
Payload keys live outside Run prefixes and are reclaimed through the outbox.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Literal, cast

import anyio
from a13n_harness import HarnessState, StateStore
from a13n_harness.compaction import CompactionInput, CompactionOperation, CompactionResult, compaction_disposition
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import lock, now, short_session, transaction
from a13n_service.infra.objects.interface import ObjectRef, read
from a13n_service.infra.outbox import prepare
from a13n_service.runs.attempts import Lease, LeaseLost, lock_thread_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import CompactionRow, ThreadRow

RETENTION = timedelta(days=7)
type Disposition = Literal["applied", "skipped", "archived", "expired"]


@dataclass(frozen=True, slots=True)
class Owner:
    operation_id: str
    thread_id: str
    workspace_id: str
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Observation:
    id: str
    thread_id: str
    input_digest: str
    input_ref: ObjectRef
    result_ref: ObjectRef | None
    status: Literal["running", "ready", "failed"]
    error_code: str | None

    @classmethod
    def of(cls, row: CompactionRow) -> Observation:
        assert row.input_ref is not None
        return cls(
            row.id,
            row.thread_id,
            row.input_digest,
            ObjectRef(**row.input_ref),
            ObjectRef(**row.result_ref) if row.result_ref else None,
            cast(Literal["running", "ready", "failed"], row.status),
            row.error_code,
        )


def key(workspace_id: str, operation_id: str, kind: Literal["input", "result"]) -> str:
    # Harness operation IDs can contain uppercase URL-safe characters; object keys cannot.
    identity = sha256(operation_id.encode()).hexdigest()
    return f"compactions/{workspace_id}/{identity}/{kind}.json"


def _cleanup(session: AsyncSession, runtime: Runtime, row: CompactionRow, current: datetime) -> None:
    delivery = prepare(
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        kind="checkpoint_cleanup",
        target={"run_id": row.run_id},
        payload={"keys": [key(row.workspace_id, row.id, kind) for kind in ("input", "result")]},
    )
    # A fenced in-flight upload gets its bounded I/O window before deletion.
    delivery.available_at = max(current, row.lease_expires_at) + timedelta(seconds=runtime.settings.objects.timeout)
    session.add(delivery)


def settle(
    session: AsyncSession, runtime: Runtime, row: CompactionRow, disposition: Disposition, *, at: datetime
) -> None:
    """Caller holds Thread then operation; checkpoint callers share their pointer transaction."""
    if row.settled_at is not None:
        return
    if row.status == "running":
        row.status, row.completed_at, row.error_code = "failed", at, "compaction_interrupted"
    row.disposition, row.settled_at = disposition, at
    _cleanup(session, runtime, row, at)


async def unsettled(session: AsyncSession, thread_id: str) -> CompactionRow | None:
    return await session.scalar(
        select(CompactionRow)
        .where(CompactionRow.thread_id == thread_id, CompactionRow.settled_at.is_(None))
        .with_for_update()
    )


def _expire(session: AsyncSession, runtime: Runtime, row: CompactionRow, current: datetime) -> None:
    if row.status == "running" and row.lease_expires_at <= current:
        row.status, row.completed_at, row.error_code = "failed", current, "compaction_interrupted"
        # Upload never committed: there is no Harness operation to deliver or acknowledge.
        if row.input_ref is None:
            settle(session, runtime, row, "expired", at=current)


async def reserve(runtime: Runtime, lease: Lease, input: CompactionInput) -> Owner | None:
    """Reserve once under current Run authority. None means an existing operation owns the slot."""
    if input.basis.thread_id != lease.thread_id:
        raise ValueError("Compaction input belongs to another Thread")
    data = input.model_dump_json().encode()
    async with transaction(runtime.storage) as session:
        thread, run, _, current = await lock_thread_lease(session, lease)
        if thread.archived_at is not None or thread.origin == "child" or run.cancel_requested_at is not None:
            raise LeaseLost()
        row = await unsettled(session, thread.id)
        if row is not None:
            _expire(session, runtime, row, current)
            if row.settled_at is None:
                if row.id == input.basis.operation_id and row.input_digest != sha256(data).hexdigest():
                    raise ValueError("Compaction identity was reused with different input")
                return None
            await session.flush()
        if await session.get(CompactionRow, input.basis.operation_id) is not None:
            raise ValueError("A settled compaction identity cannot be reused")
        owner = Owner(input.basis.operation_id, thread.id, lease.workspace_id, token_urlsafe(32))
        session.add(
            CompactionRow(
                id=owner.operation_id,
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                thread_id=lease.thread_id,
                run_id=lease.run_id,
                attempt_id=lease.attempt_id,
                owner_token_hash=secret_hash(owner.token),
                lease_expires_at=current + timedelta(seconds=runtime.settings.worker.lease_seconds),
                input_digest=sha256(data).hexdigest(),
                status="running",
            )
        )
    return owner


async def lock_owner(session: AsyncSession, owner: Owner) -> tuple[CompactionRow, datetime]:
    """Call after any Thread/Run locks; no parent Run lease or cancellation participates."""
    row = await lock(session, CompactionRow, owner.operation_id)
    current = await now(session)
    if (
        row is None
        or row.thread_id != owner.thread_id
        or row.workspace_id != owner.workspace_id
        or not hmac.compare_digest(row.owner_token_hash, secret_hash(owner.token))
        or row.settled_at is not None
        or row.status != "running"
        or row.lease_expires_at <= current
    ):
        raise LeaseLost()
    return row, current


async def renew(runtime: Runtime, owner: Owner) -> None:
    async with transaction(runtime.storage) as session:
        row, current = await lock_owner(session, owner)
        row.lease_expires_at = current + timedelta(seconds=runtime.settings.worker.lease_seconds)


async def publish(runtime: Runtime, owner: Owner, value: CompactionInput | CompactionResult) -> None:
    """Retry-safe publication of the same bytes; generation belongs outside this function."""
    kind = "input" if isinstance(value, CompactionInput) else "result"
    data = value.model_dump_json().encode()
    object_key = key(owner.workspace_id, owner.operation_id, kind)
    async with transaction(runtime.storage) as session:
        # Reconcile an uncertain commit without another upload.
        row = await lock(session, CompactionRow, owner.operation_id)
        if row is not None and hmac.compare_digest(row.owner_token_hash, secret_hash(owner.token)):
            previous = row.input_ref if kind == "input" else row.result_ref
            if previous is not None:
                if previous["digest"] != sha256(data).hexdigest():
                    raise ValueError("Compaction publication changed its bytes")
                return
        started = time.monotonic()
        row, current = await lock_owner(session, owner)
        deadline = started + (row.lease_expires_at - current).total_seconds()
    # Transaction exit can wait for PostgreSQL. Recheck the confirmed local deadline
    # immediately before I/O, so a delayed commit cannot write after reclamation.
    if deadline - time.monotonic() <= runtime.settings.objects.timeout:
        raise LeaseLost()
    try:
        with anyio.fail_after(runtime.settings.objects.timeout):
            ref = await runtime.objects.put(object_key, data, content_type="application/json")
        async with transaction(runtime.storage) as session:
            row, current = await lock_owner(session, owner)
            if isinstance(value, CompactionInput):
                if ref.digest != row.input_digest:
                    raise ValueError("Compaction input changed after reservation")
                row.input_ref = asdict(ref)
            else:
                if row.input_ref is None:
                    raise ValueError("Compaction input must be durable before generation")
                row.status, row.completed_at, row.result_ref = "ready", current, asdict(ref)
    finally:
        # Archive/expiry may have raced the upload. Requeue deletion rather than relying on
        # an earlier cleanup that could have observed the object before it existed.
        with anyio.CancelScope(shield=True):
            async with transaction(runtime.storage) as session:
                row = await lock(session, CompactionRow, owner.operation_id)
                if row is not None and row.settled_at is not None:
                    _cleanup(session, runtime, row, await now(session))


async def fail(runtime: Runtime, owner: Owner, code: str) -> None:
    async with transaction(runtime.storage) as session:
        try:
            row, current = await lock_owner(session, owner)
        except LeaseLost:
            return
        row.status, row.completed_at, row.error_code = "failed", current, code
        if row.input_ref is None:
            settle(session, runtime, row, "expired", at=current)


async def observe(runtime: Runtime, thread_id: str) -> Observation | None:
    while True:
        async with transaction(runtime.storage) as session:
            thread = await lock(session, ThreadRow, thread_id)
            if thread is None or thread.archived_at is not None:
                return None
            row = await unsettled(session, thread_id)
            if row is None:
                return None
            _expire(session, runtime, row, await now(session))
            if row.settled_at is not None:
                return None
            if row.input_ref is not None:
                return Observation.of(row)
        # Admission is still uploading. Wait outside the session, never start another generation.
        await asyncio.sleep(0.05)


async def load(runtime: Runtime, observed: Observation) -> CompactionOperation:
    with anyio.fail_after(runtime.settings.objects.timeout):
        input = CompactionInput.model_validate_json(await read(runtime.objects, observed.input_ref))
        result = (
            CompactionResult.model_validate_json(await read(runtime.objects, observed.result_ref))
            if observed.result_ref is not None
            else None
        )
    return CompactionOperation(input=input, status=observed.status, result=result, error_code=observed.error_code)


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    operation_id: str
    input_digest: str
    disposition: Literal["applied", "skipped"]


async def acknowledgement(runtime: Runtime, state: HarnessState, state_store: StateStore) -> Acknowledgement | None:
    observed = await observe(runtime, state.thread_id)
    if observed is None:
        return None
    operation = await load(runtime, observed)
    disposition = await compaction_disposition(state, basis=operation.input.basis, state_store=state_store)
    return Acknowledgement(observed.id, observed.input_digest, disposition) if disposition is not None else None


async def acknowledge(
    session: AsyncSession, runtime: Runtime, thread_id: str, acknowledgement: Acknowledgement | None, *, at: datetime
) -> None:
    """Only call inside the transaction that commits the exact inspected checkpoint."""
    if acknowledgement is None:
        return
    row = await unsettled(session, thread_id)
    if row is not None and row.id == acknowledgement.operation_id:
        if row.input_digest != acknowledgement.input_digest:
            raise ValueError("Compaction input identity changed")
        settle(session, runtime, row, acknowledgement.disposition, at=at)


async def archive(session: AsyncSession, runtime: Runtime, thread_id: str, *, at: datetime) -> None:
    row = await unsettled(session, thread_id)
    if row is not None:
        settle(session, runtime, row, "archived", at=at)


async def sweep(runtime: Runtime, *, limit: int = 100) -> None:
    """Bounded maintenance: expired owners fail; old results retire only on idle Threads."""
    async with short_session(runtime.storage) as session:
        current = await now(session)
        candidates = list(
            await session.scalars(
                select(CompactionRow.thread_id)
                .join(ThreadRow, ThreadRow.id == CompactionRow.thread_id)
                .where(
                    CompactionRow.settled_at.is_(None),
                    or_(
                        and_(CompactionRow.status == "running", CompactionRow.lease_expires_at <= current),
                        and_(
                            ThreadRow.current_run_id.is_(None),
                            CompactionRow.completed_at <= current - RETENTION,
                        ),
                    ),
                )
                .order_by(CompactionRow.created_at)
                .limit(limit)
            )
        )
    for thread_id in candidates:
        async with transaction(runtime.storage) as session:
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.id == thread_id).with_for_update(skip_locked=True)
            )
            if thread is None:
                continue
            row = await unsettled(session, thread_id)
            if row is None:
                continue
            current = await now(session)
            _expire(session, runtime, row, current)
            if thread.archived_at is not None:
                settle(session, runtime, row, "archived", at=current)
            elif (
                thread.current_run_id is None
                and row.completed_at is not None
                and row.completed_at <= current - RETENTION
            ):
                settle(session, runtime, row, "expired", at=current)
