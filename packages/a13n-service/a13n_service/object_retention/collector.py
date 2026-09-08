"""Fenced, bounded physical reclamation with restart recovery from object claims."""

import logging
from dataclasses import dataclass
from datetime import timedelta

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.ids import new_object_id
from a13n_service.storage import (
    ObjectConflict,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    short_session,
    transaction,
)
from a13n_service.temporal import Clock, assume_utc, utc_now

from .models import ObjectPublicationRecord
from .ownership import retained_owner
from .persistence import lock_publication

logger = logging.getLogger("a13n_service.object_retention")


@dataclass(frozen=True, slots=True)
class _Claim:
    key: str
    generation: str
    version: str


class ObjectCollector:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: ObjectStore,
        *,
        minimum_age: timedelta,
        batch_limit: int,
        item_timeout_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._minimum_age = minimum_age
        self._limit = batch_limit
        self._timeout = item_timeout_seconds
        self._clock = clock
        self._cursor: str | None = None
        self._after_recovery_key = ""

    async def scan(self) -> Sweep:
        page = await self._objects.list(prefix="organizations/", cursor=self._cursor, limit=self._limit)
        self._cursor = page.cursor
        cutoff = self._clock() - self._minimum_age
        keys = tuple(item.key for item in page.items if assume_utc(item.modified_at) < cutoff)
        result = await self._process(keys)
        return Sweep(
            examined=len(page.items),
            completed=result.completed,
            deferred=result.deferred + len(page.items) - len(keys),
            failed=result.failed,
            oldest_age_seconds=max(
                ((self._clock() - assume_utc(item.modified_at)).total_seconds() for item in page.items), default=None
            ),
        )

    async def recover(self) -> Sweep:
        now = self._clock()
        async with short_session(self._sessions) as database:
            keys = tuple(
                await database.scalars(
                    select(ObjectPublicationRecord.key)
                    .where(
                        ObjectPublicationRecord.key > self._after_recovery_key,
                        ObjectPublicationRecord.lease_expires_at <= now,
                    )
                    .order_by(ObjectPublicationRecord.key)
                    .limit(self._limit)
                )
            )
        self._after_recovery_key = keys[-1] if keys else ""
        return await self._process(keys)

    async def _process(self, keys: tuple[str, ...]) -> Sweep:
        completed = deferred = failed = 0
        for key in keys:
            try:
                with anyio.fail_after(self._timeout):
                    progressed = await self.collect_unowned(key)
            except (ObjectConflict, TimeoutError):
                deferred += 1
            except ObjectStoreError:
                failed += 1
                logger.warning("object_collection_retry", extra={"event": "object_collection_retry", "object_key": key})
            else:
                completed += int(progressed)
                deferred += int(not progressed)
        return Sweep(examined=len(keys), completed=completed, deferred=deferred, failed=failed)

    async def collect_unowned(self, key: str) -> bool:
        """Attempt one candidate under this collector's age policy and durable fence."""
        try:
            info = await self._objects.stat(key)
        except ObjectNotFound:
            return await self._settle_absent(key)
        now = self._clock()
        cutoff = now - self._minimum_age
        if assume_utc(info.modified_at) >= cutoff:
            return False
        async with transaction(self._sessions) as database:
            record = await lock_publication(database, key, observed_at=info.modified_at)
            if record.lease_expires_at is not None and assume_utc(record.lease_expires_at) > now:
                return False
            if record.phase != "collecting" and assume_utc(record.updated_at) >= cutoff:
                return False
            if await retained_owner(database, key, now=now) is not False:
                return False
            record.generation = new_object_id("opg")
            record.phase = "collecting"
            record.lease_expires_at = now + timedelta(seconds=self._timeout)
            record.object_version = info.version
            record.updated_at = now
            claim = _Claim(key, record.generation, info.version)
        # No session survives physical deletion. Lost acknowledgements leave the
        # claim recoverable; late deletion can affect only this exact version.
        try:
            await self._objects.delete(claim.key, if_match=claim.version)
        except ObjectNotFound:
            pass
        async with transaction(self._sessions) as database:
            finished = await database.scalar(
                update(ObjectPublicationRecord)
                .where(
                    ObjectPublicationRecord.key == claim.key,
                    ObjectPublicationRecord.generation == claim.generation,
                    ObjectPublicationRecord.phase == "collecting",
                    ObjectPublicationRecord.lease_expires_at > self._clock(),
                )
                .values(phase="collected", lease_expires_at=None, updated_at=self._clock())
                .returning(ObjectPublicationRecord.key)
            )
        return finished is not None

    async def _settle_absent(self, key: str) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as database:
            record = await lock_publication(database, key, observed_at=now)
            if record.lease_expires_at is None or assume_utc(record.lease_expires_at) > now:
                return False
            if await retained_owner(database, key, now=now) is not False:
                return False
            record.phase = "collected"
            record.lease_expires_at = None
            record.updated_at = now
        return True
