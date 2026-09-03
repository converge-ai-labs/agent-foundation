"""Bounded cleanup of protected raw evidence and terminal Ingress identities."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import anyio
from pydantic import ValidationError
from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import ObjectConflict, ObjectStore, ObjectStoreError, transaction

from .admission_domain import ProtectedRawRef
from .admission_models import IngressAdmissionRecord, IngressBatchEventRecord, IngressBatchRecord

logger = logging.getLogger("a13n_service.connectivity.ingress.retention")

_RAW_KEY = re.compile(r"^tenants/[^/]+/workspaces/[^/]+/connectivity/raw/version-1/[^/]+/[0-9a-f]{64}/[0-9a-f]{64}$")


class IngressRetentionReconciler:
    """Expire raw evidence and terminal deduplication facts in bounded slices."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: ObjectStore,
        *,
        poll_interval_seconds: float = 60,
        object_grace_seconds: float = 3600,
        batch_size: int = 25,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._poll_interval_seconds = poll_interval_seconds
        self._object_grace_seconds = object_grace_seconds
        self._batch_size = batch_size
        self._clock = clock
        self._object_cursor: str | None = None

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception(
                    "ingress_retention_reconcile_failed",
                    extra={"event": "ingress_retention_reconcile_failed"},
                )
            await anyio.sleep(self._poll_interval_seconds)

    async def reconcile_once(self) -> int:
        cleaned = await self._clean_expired_raw()
        cleaned += await self._clean_terminal_admissions()
        cleaned += await self._clean_orphan_raw()
        return cleaned

    async def _clean_expired_raw(self) -> int:
        now = self._clock()
        refs: list[ProtectedRawRef] = []
        async with transaction(self._sessions) as session:
            records = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord)
                        .where(IngressAdmissionRecord.raw_ref_json.is_not(None))
                        .order_by(IngressAdmissionRecord.created_at, IngressAdmissionRecord.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for record in records:
                try:
                    ref = ProtectedRawRef.model_validate(record.raw_ref_json)
                except ValidationError:
                    continue
                if _utc(ref.expires_at) <= now:
                    record.raw_ref_json = None
                    refs.append(ref)
        for ref in refs:
            await self._delete_object(ref.object_key)
        return len(refs)

    async def _clean_terminal_admissions(self) -> int:
        now = self._clock()
        async with transaction(self._sessions) as session:
            batches = tuple(
                (
                    await session.scalars(
                        select(IngressBatchRecord)
                        .where(
                            IngressBatchRecord.status.in_(("accepted", "rejected")),
                            IngressBatchRecord.terminal_at.is_not(None),
                            ~exists(
                                select(1)
                                .select_from(IngressBatchEventRecord)
                                .join(
                                    IngressAdmissionRecord,
                                    IngressAdmissionRecord.id == IngressBatchEventRecord.admission_id,
                                )
                                .where(
                                    IngressBatchEventRecord.batch_id == IngressBatchRecord.id,
                                    IngressAdmissionRecord.dedup_expires_at > now,
                                )
                            ),
                        )
                        .order_by(IngressBatchRecord.terminal_at, IngressBatchRecord.id)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            batch_ids = tuple(record.id for record in batches)
            if batch_ids:
                await session.execute(
                    delete(IngressBatchEventRecord).where(IngressBatchEventRecord.batch_id.in_(batch_ids))
                )
                await session.execute(delete(IngressBatchRecord).where(IngressBatchRecord.id.in_(batch_ids)))
            admission_ids = tuple(
                (
                    await session.scalars(
                        select(IngressAdmissionRecord.id)
                        .where(
                            IngressAdmissionRecord.status.in_(("accepted", "rejected")),
                            IngressAdmissionRecord.dedup_expires_at <= now,
                            ~exists(select(1).where(IngressBatchEventRecord.admission_id == IngressAdmissionRecord.id)),
                        )
                        .order_by(IngressAdmissionRecord.dedup_expires_at, IngressAdmissionRecord.id)
                        .limit(self._batch_size)
                    )
                ).all()
            )
            if admission_ids:
                await session.execute(
                    delete(IngressAdmissionRecord).where(IngressAdmissionRecord.id.in_(admission_ids))
                )
            return len(batch_ids) + len(admission_ids)

    async def _clean_orphan_raw(self) -> int:
        cutoff = self._clock() - timedelta(seconds=self._object_grace_seconds)
        try:
            page = await self._objects.list(
                prefix="tenants/",
                cursor=self._object_cursor,
                limit=self._batch_size,
            )
        except ObjectStoreError:
            return 0
        self._object_cursor = page.cursor
        cleaned = 0
        for item in page.items:
            if _utc(item.modified_at) > cutoff or _RAW_KEY.fullmatch(item.key) is None:
                continue
            if not await self._raw_object_exists(item.key):
                cleaned += await self._delete_object(item.key, version=item.version)
        return cleaned

    async def _raw_object_exists(self, object_key: str) -> bool:
        async with transaction(self._sessions) as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            expression = (
                "raw_ref_json::jsonb ->> 'object_key'"
                if dialect == "postgresql"
                else "json_extract(raw_ref_json, '$.object_key')"
            )
            return bool(
                await session.scalar(
                    text(f"SELECT EXISTS (SELECT 1 FROM ingress_admissions WHERE {expression} = :object_key)"),
                    {"object_key": object_key},
                )
            )

    async def _delete_object(self, key: str, *, version: str | None = None) -> int:
        try:
            await self._objects.delete(key, if_match=version)
        except (ObjectConflict, ObjectStoreError):
            return 0
        return 1


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
