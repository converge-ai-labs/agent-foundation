"""Lease-based ConnectorProvider setup reconciliation."""

from __future__ import annotations

from contextlib import aclosing
from datetime import timedelta

from anyio import move_on_after, sleep
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connectors.contracts import (
    AdapterConnectionStatus,
    ConnectorProviderError,
)
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import ConnectorConnectionStatus, ConnectorConnectionStatusReason
from .errors import ConnectorError
from .management import configure_provider, require_active_provider
from .models import ConnectorConnectionRecord, ConnectorSetupAttemptRecord
from .setup import ConnectorSetupCoordinator, _setup_context


class ConnectorReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        setup: ConnectorSetupCoordinator,
        *,
        instance_id: str,
        poll_interval_seconds: float,
        lease_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._setup = setup
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def run(self) -> None:
        while True:
            productive = await self.reconcile_once()
            await sleep(0 if productive else self._poll_interval_seconds)

    async def reconcile_once(self) -> bool:
        if await self._expire_attempt():
            return True
        attempt = await self._claim_attempt()
        if attempt is not None:
            await self._reconcile_attempt(*attempt)
            return True
        return False

    async def _expire_attempt(self) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await session.scalar(
                select(ConnectorConnectionRecord)
                .join(
                    ConnectorSetupAttemptRecord,
                    ConnectorSetupAttemptRecord.connector_connection_id == ConnectorConnectionRecord.id,
                )
                .where(
                    ConnectorSetupAttemptRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    ConnectorSetupAttemptRecord.expires_at <= now,
                )
                .order_by(ConnectorSetupAttemptRecord.expires_at, ConnectorSetupAttemptRecord.id)
                .limit(1)
                .with_for_update(of=ConnectorConnectionRecord, skip_locked=True)
            )
            if connection is None:
                return False
            attempt = await session.scalar(
                select(ConnectorSetupAttemptRecord)
                .where(
                    ConnectorSetupAttemptRecord.connector_connection_id == connection.id,
                    ConnectorSetupAttemptRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    ConnectorSetupAttemptRecord.expires_at <= now,
                )
                .order_by(ConnectorSetupAttemptRecord.expires_at, ConnectorSetupAttemptRecord.id)
                .limit(1)
                .with_for_update()
            )
            if attempt is None:
                return False
            if attempt.status == "starting":
                attempt.last_error_code = "setup_outcome_unknown"
            attempt.status = "expired"
            attempt.updated_at = now
            if connection.setup_generation == attempt.generation and connection.status == "pending":
                connection.status = ConnectorConnectionStatus.action_required.value
                connection.status_reason = ConnectorConnectionStatusReason.reauthorization_required.value
                connection.version += 1
                connection.updated_at = now
            return True

    async def _claim_attempt(self) -> tuple[str, int] | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            attempt = await session.scalar(
                select(ConnectorSetupAttemptRecord)
                .where(
                    ConnectorSetupAttemptRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    ConnectorSetupAttemptRecord.available_at <= now,
                    ConnectorSetupAttemptRecord.expires_at > now,
                    or_(
                        ConnectorSetupAttemptRecord.claim_expires_at.is_(None),
                        ConnectorSetupAttemptRecord.claim_expires_at <= now,
                    ),
                )
                .order_by(ConnectorSetupAttemptRecord.available_at, ConnectorSetupAttemptRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if attempt is None:
                return None
            attempt.claim_generation += 1
            attempt.claim_owner = self._instance_id
            attempt.claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return attempt.id, attempt.claim_generation

    async def _reconcile_attempt(self, attempt_id: str, claim_generation: int) -> None:
        try:
            async with short_session(self._sessions) as session:
                attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
                if attempt is None:
                    return
                status = attempt.status
            if status in {"pending", "starting"}:
                try:
                    await self._setup.start_attempt(
                        attempt_id,
                        claim_owner=self._instance_id,
                        claim_generation=claim_generation,
                    )
                except ConnectorError as error:
                    await self._defer_attempt(attempt_id, claim_generation, code=error.code, increment=False)
                return
            snapshot = await self._setup.attempt_snapshot(attempt_id)
            require_active_provider(snapshot.connector)
            if status == "attached" and snapshot.attempt.supports_verified_callback:
                await self._defer_attempt(attempt_id, claim_generation, code=None, increment=False)
                return
            if snapshot.attempt.setup_ref is None:
                raise ConnectorError(
                    "setup_unavailable", "Setup reference is unavailable.", category=ErrorCategory.conflict
                )
            runtime = configure_provider(self._adapters, snapshot.connector, snapshot.credentials)
            async with aclosing(runtime):
                inspection = await runtime.inspect_setup(
                    setup_ref=snapshot.attempt.setup_ref,
                    context=_setup_context(snapshot.attempt, callback_url=self._setup.callback_url()),
                )
            if inspection is None or inspection.status is AdapterConnectionStatus.pending:
                await self._defer_attempt(attempt_id, claim_generation, code=None)
            else:
                await self._setup.finish_attempt(
                    attempt_id,
                    inspection,
                    claim_owner=self._instance_id,
                    claim_generation=claim_generation,
                )
        except ConnectorProviderError as error:
            if error.retryable or error.outcome_unknown:
                await self._defer_attempt(attempt_id, claim_generation, code=error.code)
            else:
                await self._setup.fail_attempt(
                    attempt_id, code=error.code, claim_owner=self._instance_id, claim_generation=claim_generation
                )
        except ConnectorError as error:
            if error.code == "connection_substitution":
                await self._setup.fail_attempt(
                    attempt_id, code=error.code, claim_owner=self._instance_id, claim_generation=claim_generation
                )
            else:
                await self._defer_attempt(attempt_id, claim_generation, code=error.code)
        finally:
            with move_on_after(5, shield=True):
                await self._setup.release_attempt(attempt_id, owner=self._instance_id, generation=claim_generation)

    async def _defer_attempt(
        self,
        attempt_id: str,
        claim_generation: int,
        *,
        code: str | None,
        increment: bool = True,
    ) -> None:
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.status not in {"pending", "starting", "attached", "reserved"}
                or not _owns_attempt(attempt, self._instance_id, claim_generation)
            ):
                return
            attempt.available_at = max(assume_utc(attempt.available_at), self._clock() + timedelta(seconds=5))
            if increment:
                attempt.attempt_count += 1
            attempt.last_error_code = code


def _owns_attempt(
    attempt: ConnectorSetupAttemptRecord | None,
    owner: str,
    generation: int,
) -> bool:
    return attempt is not None and attempt.claim_owner == owner and attempt.claim_generation == generation
