"""Object I/O outside transactions, followed by fenced publication completion."""

from collections.abc import Mapping
from datetime import timedelta

import anyio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import ObjectConflict, ObjectStore, ObjectStoreUnavailable, transaction
from a13n_service.storage.object_store import ByteRange, ObjectInfo, ObjectPage, ObjectSource
from a13n_service.temporal import Clock, assume_utc, utc_now

from .models import ObjectPublicationRecord
from .persistence import lock_publication


class PublicationObjectStore:
    """Service wrapper; the raw backend retains its provider-neutral contract."""

    def __init__(
        self,
        objects: ObjectStore,
        sessions: async_sessionmaker[AsyncSession],
        *,
        timeout_seconds: float = 120,
        clock: Clock = utc_now,
    ) -> None:
        self.backend = objects
        self._sessions = sessions
        self._timeout = timeout_seconds
        self._clock = clock

    async def put(
        self,
        key: str,
        source: ObjectSource,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo:
        if if_match is not None:
            # Checkpoint replacement belongs to an already accepted Run. Its
            # retained owner pins the namespace; the Run writer owns this CAS.
            return await self.backend.put(
                key,
                source,
                content_type=content_type,
                metadata=metadata,
                if_none_match=if_none_match,
                if_match=if_match,
            )
        if not if_none_match and if_match is None:
            raise ObjectStoreUnavailable("Service publication requires a conditional write")
        with anyio.fail_after(self._timeout):
            generation = await self._begin(key)
            conflict = False
            try:
                info = await self.backend.put(
                    key,
                    source,
                    content_type=content_type,
                    metadata=metadata,
                    if_none_match=if_none_match,
                    if_match=if_match,
                )
            except ObjectConflict:
                conflict = True
                # Reuse exactly the stored bytes, never the losing writer's body.
                # A fresh provider version fences every delayed former collector,
                # even when immutable content and its logical digest are identical.
                async with self.backend.open(key) as reader:
                    info = await self.backend.put(
                        key,
                        reader,
                        content_type=reader.info.content_type,
                        metadata=reader.info.metadata,
                        if_match=reader.info.version,
                    )
            await self._finish(key, generation, info.version)
            if conflict:
                raise ObjectConflict("An existing immutable object was retained")
            return info

    async def _begin(self, key: str) -> str:
        now = self._clock()
        async with transaction(self._sessions) as database:
            record = await lock_publication(database, key, observed_at=now)
            if record.lease_expires_at is not None and assume_utc(record.lease_expires_at) > now:
                raise ObjectStoreUnavailable("Object publication or collection is in progress")
            record.generation = new_object_id("opg")
            record.phase = "publishing"
            record.lease_expires_at = now + timedelta(seconds=self._timeout)
            record.updated_at = now
            return record.generation

    async def _finish(self, key: str, generation: str, version: str) -> None:
        now = self._clock()
        async with transaction(self._sessions) as database:
            completed = await database.scalar(
                update(ObjectPublicationRecord)
                .where(
                    ObjectPublicationRecord.key == key,
                    ObjectPublicationRecord.generation == generation,
                    ObjectPublicationRecord.phase == "publishing",
                    ObjectPublicationRecord.lease_expires_at > now,
                )
                .values(phase="ready", object_version=version, lease_expires_at=None, updated_at=now)
                .returning(ObjectPublicationRecord.key)
            )
            if completed is None:
                raise ObjectStoreUnavailable("Object publication authority expired")

    def open(self, key: str, *, byte_range: ByteRange | None = None):
        return self.backend.open(key, byte_range=byte_range)

    async def stat(self, key: str) -> ObjectInfo:
        return await self.backend.stat(key)

    async def delete(self, key: str, *, if_match: str | None = None) -> None:
        await self.backend.delete(key, if_match=if_match)

    async def list(self, *, prefix: str = "", cursor: str | None = None, limit: int = 100) -> ObjectPage:
        return await self.backend.list(prefix=prefix, cursor=cursor, limit=limit)
