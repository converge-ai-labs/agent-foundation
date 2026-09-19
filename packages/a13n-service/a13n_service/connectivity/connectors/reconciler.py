"""Lease-based ConnectorProvider setup reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector import ConnectorHttpClient, ConnectorProviderDefinition
from a13n_harness.providers.connector.contracts import (
    AdapterConnectionStatus,
    ConnectorProviderError,
    SetupCompletionMethod,
)
from anyio import move_on_after
from sqlalchemy import and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.background import PeriodicTask, Sweep
from a13n_service.connectivity.connections.domain import ConnectionStatus, ConnectionStatusReason
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .errors import ConnectorError
from .management import open_provider, require_active_provider
from .models import ConnectorAuthorizationRecord, ConnectorConnectionRecord
from .setup import ConnectorSetupCoordinator, _setup_context


class ConnectorReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        connectors: ProviderCatalog[ConnectorProviderDefinition],
        connector_http: ConnectorHttpClient | None,
        setup: ConnectorSetupCoordinator,
        *,
        instance_id: str,
        poll_interval_seconds: float,
        lease_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._connectors = connectors
        self._connector_http = connector_http
        self._setup = setup
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def run(self) -> None:
        await PeriodicTask(
            "connector_setup_reconciliation",
            self.scan,
            interval_seconds=self._poll_interval_seconds,
            timeout_seconds=self._lease_seconds,
        ).run()

    async def scan(self) -> Sweep:
        if await self._expire_attempt():
            return Sweep(examined=1, completed=1)
        claim = await self._claim_attempt()
        if claim is None:
            return Sweep()
        await self._reconcile_attempt(*claim)
        async with short_session(self._sessions) as session:
            record = await session.get(ConnectorAuthorizationRecord, claim[0])
            complete = record is not None and record.status not in ("pending", "starting", "attached", "reserved")
            age = max(0, (self._clock() - assume_utc(record.created_at)).total_seconds()) if record else None
        return Sweep(examined=1, completed=int(complete), deferred=int(not complete), oldest_age_seconds=age)

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
                    ConnectorAuthorizationRecord,
                    ConnectorAuthorizationRecord.connection_id == ConnectorConnectionRecord.id,
                )
                .where(
                    ConnectorAuthorizationRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    ConnectorAuthorizationRecord.expires_at <= now,
                )
                .order_by(ConnectorAuthorizationRecord.expires_at, ConnectorAuthorizationRecord.id)
                .limit(1)
                .with_for_update(of=ConnectorConnectionRecord, skip_locked=True)
            )
            if connection is None:
                return False
            attempt = await session.scalar(
                select(ConnectorAuthorizationRecord)
                .where(
                    ConnectorAuthorizationRecord.connection_id == connection.id,
                    ConnectorAuthorizationRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    ConnectorAuthorizationRecord.expires_at <= now,
                )
                .order_by(ConnectorAuthorizationRecord.expires_at, ConnectorAuthorizationRecord.id)
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
                connection.status = ConnectionStatus.action_required.value
                connection.status_reason = ConnectionStatusReason.reauthorization_required.value
                connection.version += 1
                connection.updated_at = now
            return True

    async def _claim_attempt(self) -> tuple[str, int] | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            attempt = await session.scalar(
                select(ConnectorAuthorizationRecord)
                .where(
                    ConnectorAuthorizationRecord.status.in_(("pending", "starting", "attached", "reserved")),
                    not_(
                        and_(
                            ConnectorAuthorizationRecord.status == "attached",
                            ConnectorAuthorizationRecord.completion_method != SetupCompletionMethod.polling,
                            ConnectorAuthorizationRecord.browser_binding_digest.is_not(None),
                        )
                    ),
                    ConnectorAuthorizationRecord.available_at <= now,
                    ConnectorAuthorizationRecord.expires_at > now,
                    or_(
                        ConnectorAuthorizationRecord.claim_expires_at.is_(None),
                        ConnectorAuthorizationRecord.claim_expires_at <= now,
                    ),
                )
                .order_by(ConnectorAuthorizationRecord.available_at, ConnectorAuthorizationRecord.id)
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
                attempt = await session.get(ConnectorAuthorizationRecord, attempt_id)
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
            if status == "attached" and snapshot.attempt.completion_method != SetupCompletionMethod.polling:
                await self._defer_attempt(attempt_id, claim_generation, code=None, increment=False)
                return
            if snapshot.attempt.setup_ref is None:
                raise ConnectorError(
                    "setup_unavailable", "Setup reference is unavailable.", category=ErrorCategory.conflict
                )
            async with open_provider(
                self._connectors, self._connector_http, snapshot.connector, snapshot.credentials
            ) as runtime:
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
            attempt = await session.get(ConnectorAuthorizationRecord, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.status not in {"pending", "starting", "attached", "reserved"}
                or not _owns_attempt(attempt, self._instance_id, claim_generation, self._clock())
            ):
                return
            attempt.available_at = max(assume_utc(attempt.available_at), self._clock() + timedelta(seconds=5))
            if increment:
                attempt.attempt_count += 1
            if code is not None and (increment or attempt.last_error_code is None):
                attempt.last_error_code = code


def _owns_attempt(
    attempt: ConnectorAuthorizationRecord | None,
    owner: str,
    generation: int,
    now: datetime,
) -> bool:
    return (
        attempt is not None
        and attempt.claim_owner == owner
        and attempt.claim_generation == generation
        and attempt.claim_expires_at is not None
        and assume_utc(attempt.claim_expires_at) > now
    )
