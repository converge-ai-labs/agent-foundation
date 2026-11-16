"""Private Thread object catalog over the Service's existing immutable byte backend."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import Literal, cast
from uuid import uuid4

from a13n_harness.objects import ObjectInfo, ObjectStoreError, object_not_found
from a13n_logging import get_logger
from anyio import fail_after
from sqlalchemy import select

from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.interface import ObjectRef, read
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ThreadObjectRow, ThreadRow
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize


def _info(row: ThreadObjectRow) -> ObjectInfo:
    return ObjectInfo(
        object_id=row.id,
        media_type=row.content_type,
        size=row.size,
        digest=row.digest,
        name=row.name,
        retention=cast(Literal["temporary", "stored"], row.retention),
    )


class ThreadObjectStore:
    """Each operation re-proves Run/retained-call authority; no SQL scope spans blob I/O.

    The Thread row serializes catalog mutations, including promotion versus LRU
    eviction. Tombstones retain cleanup ownership across worker loss. Inline
    children use the owning Thread; independent child/fork Threads do not inherit
    catalog authority from copied model history.
    """

    def __init__(
        self,
        runtime: Runtime,
        lease: Lease,
        authority: ExecutionAuthority,
    ) -> None:
        self.runtime, self.lease, self.authority = runtime, lease, authority
        self.policy = runtime.settings.object_store
        self.max_count = self.policy.max_temporary_objects
        self.max_bytes = self.policy.max_temporary_bytes

    async def _authorize(self, session) -> None:
        # Match checkpoint, interrupt and archive: Thread -> Run -> Attempt/call.
        thread = await session.scalar(select(ThreadRow).where(ThreadRow.id == self.lease.thread_id).with_for_update())
        if thread is None:
            raise LeaseLost()
        run, principal = await lock_owner(session, self.runtime, self.lease)
        if (run.organization_id, run.workspace_id, run.thread_id) != (
            self.lease.organization_id,
            self.lease.workspace_id,
            self.lease.thread_id,
        ):
            raise LeaseLost()
        authorize(
            principal,
            WorkspaceScope(self.lease.organization_id, self.lease.workspace_id),
            "run",
            authority=self.authority,
        )

    def _query(self):
        return select(ThreadObjectRow).where(
            ThreadObjectRow.organization_id == self.lease.organization_id,
            ThreadObjectRow.workspace_id == self.lease.workspace_id,
            ThreadObjectRow.thread_id == self.lease.thread_id,
        )

    async def _row(self, session, object_id: str) -> ThreadObjectRow:
        row = await session.scalar(self._query().where(ThreadObjectRow.id == object_id))
        if row is None or row.retention not in {"temporary", "stored"}:
            raise object_not_found()
        if row.retention == "temporary" and row.accessed_at <= (
            await now(session) - timedelta(seconds=self.policy.temporary_ttl_seconds)
        ):
            raise object_not_found()
        return row

    async def put(self, data: bytes, *, media_type: str, name: str | None = None) -> ObjectInfo:
        if len(data) > min(self.max_bytes, self.runtime.settings.objects.max_bytes):
            raise ObjectStoreError("object_too_large", "Object exceeds the Host Store byte limit.")
        identifier = f"obj_{uuid4().hex}"
        key = f"thread-objects/{self.lease.organization_id}/{self.lease.thread_id}/{identifier}"
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            row = ThreadObjectRow(
                id=identifier,
                organization_id=self.lease.organization_id,
                workspace_id=self.lease.workspace_id,
                thread_id=self.lease.thread_id,
                content_ref=key,
                digest=sha256(data).hexdigest(),
                size=len(data),
                content_type=media_type,
                name=name,
                retention="pending",
            )
            session.add(row)
        # A pending metadata row owns even an interrupted/unknown blob write.
        with fail_after(self.runtime.settings.objects.timeout):
            await self.runtime.objects.put(key, data, content_type=media_type)
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            row = await session.get(ThreadObjectRow, identifier)
            if row is None or row.retention != "pending":
                raise object_not_found()
            row.retention = "temporary"
            row.accessed_at = await now(session)
            await session.flush()
            entries = list(
                await session.scalars(
                    self._query()
                    .where(ThreadObjectRow.retention == "temporary")
                    .order_by(ThreadObjectRow.accessed_at, ThreadObjectRow.id)
                )
            )
            total = sum(item.size for item in entries)
            cutoff = await now(session) - timedelta(seconds=self.policy.temporary_ttl_seconds)
            while entries and (
                len(entries) > self.max_count or total > self.max_bytes or entries[0].accessed_at <= cutoff
            ):
                expired = entries.pop(0)
                total -= expired.size
                expired.retention = "deleted"
            result = _info(row)
        await clean(self.runtime, thread_id=self.lease.thread_id)
        return result

    async def read(self, object_id: str) -> bytes:
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            row = await self._row(session, object_id)
            row.accessed_at = await now(session)
            ref = ObjectRef(row.content_ref, row.digest, row.size, row.content_type)
        try:
            data = await read(self.runtime.objects, ref)
        except ServiceError:
            # Eviction/deletion can reclaim bytes while this read is in flight.
            # Re-prove authority and catalog availability before deciding whether
            # this is expected unavailability or a real backend failure.
            async with transaction(self.runtime.storage) as session:
                await self._authorize(session)
                await self._row(session, object_id)
            raise
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
        return data

    async def info(self, object_id: str) -> ObjectInfo:
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            return _info(await self._row(session, object_id))

    async def store(self, object_id: str) -> ObjectInfo:
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            row = await self._row(session, object_id)
            row.retention = "stored"
            return _info(row)

    async def list(self, *, offset: int = 0, limit: int = 50) -> list[ObjectInfo]:
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            entries = await session.scalars(
                self._query()
                .where(
                    (ThreadObjectRow.retention == "stored")
                    | (
                        (ThreadObjectRow.retention == "temporary")
                        & (
                            ThreadObjectRow.accessed_at
                            > await now(session) - timedelta(seconds=self.policy.temporary_ttl_seconds)
                        )
                    )
                )
                .order_by(ThreadObjectRow.created_at, ThreadObjectRow.id)
                .offset(max(0, offset))
                .limit(min(100, max(1, limit)))
            )
            return [_info(row) for row in entries]

    async def delete(self, object_id: str) -> None:
        async with transaction(self.runtime.storage) as session:
            await self._authorize(session)
            row = await self._row(session, object_id)
            row.retention = "deleted"
        await clean(self.runtime, thread_id=self.lease.thread_id)


async def clean(runtime: Runtime, *, thread_id: str | None = None) -> None:
    """Expire inactive Threads too; promotion and expiry share the Thread lock.

    A one-hour grace covers ordinary interrupted/unknown writes. A backend may
    complete after cancellation; ThreadObjectCleaner also reconciles the private
    prefix so late writes remain reclaimable after pending metadata is removed.
    Blob failures leave tombstones retryable without failing a successful import.
    """
    async with transaction(runtime.storage) as session:
        current = await now(session)
        query = (
            select(ThreadObjectRow.thread_id)
            .where(
                (ThreadObjectRow.retention == "deleted")
                | (
                    (ThreadObjectRow.retention == "pending")
                    & (ThreadObjectRow.created_at < current - timedelta(hours=1))
                )
                | (
                    (ThreadObjectRow.retention == "temporary")
                    & (
                        ThreadObjectRow.accessed_at
                        <= current - timedelta(seconds=runtime.settings.object_store.temporary_ttl_seconds)
                    )
                )
            )
            .distinct()
            .limit(runtime.settings.control.sweep_batch)
        )
        if thread_id is not None:
            query = query.where(ThreadObjectRow.thread_id == thread_id)
        threads = list(await session.scalars(query))
    for owner in threads:
        async with transaction(runtime.storage) as session:
            await session.scalar(select(ThreadRow).where(ThreadRow.id == owner).with_for_update())
            current = await now(session)
            rows = list(
                await session.scalars(
                    select(ThreadObjectRow)
                    .where(
                        ThreadObjectRow.thread_id == owner,
                        (ThreadObjectRow.retention == "deleted")
                        | (
                            (ThreadObjectRow.retention == "pending")
                            & (ThreadObjectRow.created_at < current - timedelta(hours=1))
                        )
                        | (
                            (ThreadObjectRow.retention == "temporary")
                            & (
                                ThreadObjectRow.accessed_at
                                <= current - timedelta(seconds=runtime.settings.object_store.temporary_ttl_seconds)
                            )
                        ),
                    )
                    .limit(runtime.settings.control.sweep_batch)
                )
            )
            keys = [(row.id, row.content_ref) for row in rows]
            for row in rows:
                row.retention = "deleted"
        for identifier, key in keys:
            try:
                await runtime.objects.delete(key)
                async with transaction(runtime.storage) as session:
                    row = await session.get(ThreadObjectRow, identifier)
                    if row is not None and row.retention == "deleted":
                        await session.delete(row)
            except Exception as error:
                get_logger(__name__).warning("Thread object cleanup failed", extra={"error_type": type(error).__name__})


class ThreadObjectCleaner:
    """Bounded catalog expiry plus rolling reconciliation of late backend writes.

    The cursor is sweep progress, not authority. Restarting it safely rescans the
    private prefix. Imports commit their owner before writing, so any visible key
    without catalog metadata is reclaimable, including a late cancelled write.
    """

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.after: str | None = None

    async def __call__(self) -> None:
        await clean(self.runtime)
        batch = self.runtime.settings.control.sweep_batch
        keys = await self.runtime.objects.keys("thread-objects", limit=batch, after=self.after)
        if keys:
            async with transaction(self.runtime.storage) as session:
                owned = set(
                    await session.scalars(
                        select(ThreadObjectRow.content_ref).where(ThreadObjectRow.content_ref.in_(keys))
                    )
                )
            for key in keys:
                if key not in owned:
                    await self.runtime.objects.delete(key)
                self.after = key
        if len(keys) < batch:
            self.after = None
