"""Fenced, idempotent delivery of Asset content-cleanup intents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.storage import transaction

from .models import AssetRecord
from .objects import ASSET_OBJECT_DESTINATION, AssetObjectStore

logger = logging.getLogger("a13n_service.assets.cleanup")


@dataclass(frozen=True, slots=True)
class AssetCleanupClaim:
    outbox_id: str
    asset_id: str
    generation: int
    attempt_count: int


class AssetCleanupReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: AssetObjectStore,
        *,
        poll_interval_seconds: float = 5,
        lease_seconds: float = 30,
        max_attempts: int = 10,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception("asset_cleanup_reconcile_failed", extra={"event": "asset_cleanup_reconcile_failed"})
            await anyio.sleep(self._poll_interval_seconds)

    async def reconcile_once(self, *, limit: int = 25) -> int:
        claims = await self._claim(limit=limit)
        for claim in claims:
            try:
                owner = await self._load_owner(claim.asset_id)
                if owner is None:
                    await self._finish(claim, error_code="asset_cleanup_source_missing")
                    continue
                organization_id, workspace_id = owner
                await self._objects.delete_content(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    asset_id=claim.asset_id,
                )
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                await self._finish(claim, error_code="asset_content_cleanup_failed")
            else:
                await self._finish(claim, error_code=None)
        return len(claims)

    async def _claim(self, *, limit: int) -> tuple[AssetCleanupClaim, ...]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            records = tuple(
                (
                    await session.scalars(
                        select(OutboxRecord)
                        .where(
                            OutboxRecord.source_kind == "asset",
                            OutboxRecord.destination_kind == "asset_content_cleanup",
                            OutboxRecord.destination_ref == ASSET_OBJECT_DESTINATION,
                            or_(
                                (OutboxRecord.status == "pending") & (OutboxRecord.available_at <= now),
                                (OutboxRecord.status == "publishing") & (OutboxRecord.lease_expires_at <= now),
                            ),
                        )
                        .order_by(OutboxRecord.available_at, OutboxRecord.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claims: list[AssetCleanupClaim] = []
            for record in records:
                record.status = "publishing"
                record.claim_generation += 1
                record.attempt_count += 1
                record.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
                record.updated_at = now
                record.published_at = None
                record.dead_lettered_at = None
                claims.append(
                    AssetCleanupClaim(
                        outbox_id=record.id,
                        asset_id=record.source_id,
                        generation=record.claim_generation,
                        attempt_count=record.attempt_count,
                    )
                )
            await session.flush()
            return tuple(claims)

    async def _load_owner(self, asset_id: str) -> tuple[str, str] | None:
        async with transaction(self._sessions) as session:
            row = (
                await session.execute(
                    select(AssetRecord.organization_id, AssetRecord.workspace_id).where(
                        AssetRecord.id == asset_id,
                        AssetRecord.deleted_at.is_not(None),
                    )
                )
            ).one_or_none()
            return None if row is None else (row.organization_id, row.workspace_id)

    async def _finish(self, claim: AssetCleanupClaim, *, error_code: str | None) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            record = await session.scalar(
                select(OutboxRecord)
                .where(
                    OutboxRecord.id == claim.outbox_id,
                    OutboxRecord.status == "publishing",
                    OutboxRecord.claim_generation == claim.generation,
                )
                .with_for_update()
            )
            if record is None:
                return
            record.lease_expires_at = None
            record.updated_at = now
            record.last_error_code = error_code
            if error_code is None:
                record.status = "published"
                record.published_at = now
                record.dead_lettered_at = None
            elif claim.attempt_count >= self._max_attempts or error_code == "asset_cleanup_source_missing":
                record.status = "dead_lettered"
                record.published_at = None
                record.dead_lettered_at = now
            else:
                record.status = "pending"
                record.available_at = now + timedelta(seconds=min(300, 2 ** min(claim.attempt_count, 8)))
                record.published_at = None
                record.dead_lettered_at = None
