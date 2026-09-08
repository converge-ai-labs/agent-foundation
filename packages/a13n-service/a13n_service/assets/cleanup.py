"""Fenced, idempotent delivery of Asset content-cleanup intents."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import PeriodicTask, Sweep
from a13n_service.durable_operations.outbox import OutboxClaim, claim_outbox, complete_outbox, fail_outbox
from a13n_service.durable_operations.publication import dispatch_outbox_batch
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .errors import AssetError
from .models import AssetRecord
from .objects import ASSET_OBJECT_DESTINATION, AssetObjectStore


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
        self._clock = clock or utc_now

    async def run(self) -> None:
        await PeriodicTask(
            "asset_content_cleanup",
            self.scan,
            interval_seconds=self._poll_interval_seconds,
            timeout_seconds=(self._lease_seconds + 1) * 25,
        ).run()

    async def reconcile_once(self, *, limit: int = 25) -> int:
        return (await self.scan(limit=limit)).examined

    async def scan(self, *, limit: int = 25) -> Sweep:
        claims = await self._claim(limit=limit)
        return await dispatch_outbox_batch(
            self._sessions,
            claims,
            self._publish_claim,
            timeout_seconds=self._lease_seconds,
            concurrency=1,
            clock=self._clock,
        )

    async def _publish_claim(self, claim: OutboxClaim) -> None:
        owner = await self._load_owner(claim.source_id)
        if owner is None:
            await self._finish(claim, error_code="asset_cleanup_source_missing")
            return
        organization_id, workspace_id = owner
        try:
            await self._objects.delete_content(
                organization_id=organization_id,
                workspace_id=workspace_id,
                asset_id=claim.source_id,
            )
        except AssetError:
            await self._finish(claim, error_code="asset_content_cleanup_failed")
        else:
            await self._finish(claim, error_code=None)

    async def _claim(self, *, limit: int) -> tuple[OutboxClaim, ...]:
        now = self._clock()
        async with transaction(self._sessions) as session:
            return await claim_outbox(
                session,
                source_kind="asset",
                destination_kind="asset_content_cleanup",
                destination_ref=ASSET_OBJECT_DESTINATION,
                now=now,
                lease_duration=timedelta(seconds=self._lease_seconds),
                limit=limit,
            )

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

    async def _finish(self, claim: OutboxClaim, *, error_code: str | None) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            if error_code is None:
                await complete_outbox(session, claim, completed_at=now)
                return
            await fail_outbox(
                session,
                claim,
                failed_at=now,
                error_code=error_code,
                retryable=error_code != "asset_cleanup_source_missing",
                retry_after=timedelta(seconds=min(300, 2 ** min(claim.attempt_count, 8))),
                max_attempts=self._max_attempts,
            )
