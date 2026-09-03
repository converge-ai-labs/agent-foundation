"""Lease-based Connector setup, revoke, and catalog reconciliation."""

from __future__ import annotations

from datetime import timedelta

from anyio import sleep
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import (
    AdapterConnectionStatus,
    ConnectorAdapter,
    ConnectorAdapterError,
)
from a13n_service.secrets import SecretOperation
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .catalog import ConnectorCatalogService
from .connection_access import apply_inspection, verify_inspection
from .connections import ConnectorConnectionService
from .domain import ConnectorConnectionStatus, ConnectorConnectionStatusReason
from .errors import ConnectorError
from .management import require_adapter, require_connection
from .models import ConnectorConnectionRecord, ConnectorOperationRecord, ConnectorSetupAttemptRecord
from .setup import ConnectorSetupCoordinator


class ConnectorReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[ConnectorAdapter],
        setup: ConnectorSetupCoordinator,
        connections: ConnectorConnectionService,
        catalogs: ConnectorCatalogService,
        *,
        instance_id: str,
        poll_interval_seconds: float,
        lease_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._setup = setup
        self._connections = connections
        self._catalogs = catalogs
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def run(self) -> None:
        while True:
            await sleep(self._poll_interval_seconds)
            await self.reconcile_once()

    async def reconcile_once(self) -> bool:
        if await self._expire_attempt():
            return True
        attempt = await self._claim_attempt()
        if attempt is not None:
            await self._reconcile_attempt(*attempt)
            return True
        operation = await self._claim_operation()
        if operation is not None:
            await self._reconcile_operation(*operation)
            return True
        connection_id = await self._catalog_candidate()
        if connection_id is not None:
            try:
                await self._catalogs.refresh(connection_id)
            except ConnectorError:
                await self._record_catalog_failure(connection_id)
            return True
        return False

    async def _expire_attempt(self) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            attempt = await session.scalar(
                select(ConnectorSetupAttemptRecord)
                .where(
                    ConnectorSetupAttemptRecord.status.in_(("pending", "attached", "reserved")),
                    ConnectorSetupAttemptRecord.expires_at <= now,
                )
                .order_by(ConnectorSetupAttemptRecord.expires_at, ConnectorSetupAttemptRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if attempt is None:
                return False
            attempt.status = "expired"
            attempt.updated_at = now
            connection = await require_connection(session, attempt.connector_connection_id, lock=True)
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
                    ConnectorSetupAttemptRecord.status.in_(("pending", "attached", "reserved")),
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
            if status == "pending":
                try:
                    await self._setup.start_attempt(
                        attempt_id,
                        claim_owner=self._instance_id,
                        claim_generation=claim_generation,
                    )
                except ConnectorError as error:
                    await self._defer_attempt(attempt_id, claim_generation, code=error.code, increment=False)
                return
            snapshot = await self._setup.attempt_snapshot(attempt_id, operation=SecretOperation.reconciliation)
            if status == "attached" and snapshot.attempt.supports_verified_callback:
                await self._defer_attempt(attempt_id, claim_generation, code=None, increment=False)
                return
            if snapshot.attempt.external_ref is None:
                return
            adapter = require_adapter(
                self._adapters,
                snapshot.connector.driver_key,
                snapshot.connector.config_version,
            )
            inspection = await adapter.inspect_connection(
                endpoint=snapshot.connector.endpoint,
                connector_config=snapshot.connector.config_json,
                credentials=snapshot.credentials,
                external_ref=snapshot.attempt.external_ref,
                expected_provider_key=snapshot.attempt.provider_key,
                expected_external_user_correlation=snapshot.attempt.external_user_correlation,
            )
            if inspection.status is AdapterConnectionStatus.pending:
                await self._defer_attempt(attempt_id, claim_generation, code=None)
            else:
                await self._setup.finish_attempt(
                    attempt_id,
                    inspection,
                    claim_owner=self._instance_id,
                    claim_generation=claim_generation,
                )
        except ConnectorAdapterError as error:
            await self._defer_attempt(attempt_id, claim_generation, code=error.code)
        except ConnectorError as error:
            if error.code == "connection_substitution":
                await self._setup.fail_attempt(attempt_id, code=error.code)
            else:
                await self._defer_attempt(attempt_id, claim_generation, code=error.code)
        finally:
            await self._release_attempt(attempt_id, claim_generation)

    async def _claim_operation(self) -> tuple[str, int] | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            operation = await session.scalar(
                select(ConnectorOperationRecord)
                .where(
                    ConnectorOperationRecord.status.in_(("pending", "unknown")),
                    ConnectorOperationRecord.available_at <= now,
                    or_(
                        ConnectorOperationRecord.claim_expires_at.is_(None),
                        ConnectorOperationRecord.claim_expires_at <= now,
                    ),
                )
                .order_by(ConnectorOperationRecord.available_at, ConnectorOperationRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if operation is None:
                return None
            operation.claim_generation += 1
            operation.claim_owner = self._instance_id
            operation.claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return operation.id, operation.claim_generation

    async def _reconcile_operation(self, operation_id: str, claim_generation: int) -> None:
        async with short_session(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id)
            if operation is None:
                return
            status = operation.status
        if status == "unknown" and await self._revocation_is_confirmed(operation_id, claim_generation):
            return
        try:
            await self._connections.run_revoke(
                operation_id,
                claim_owner=self._instance_id,
                claim_generation=claim_generation,
            )
        except ConnectorError:
            return

    async def _revocation_is_confirmed(self, operation_id: str, claim_generation: int) -> bool:
        async with short_session(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id)
            if operation is None:
                return True
            connection = await require_connection(session, operation.connector_connection_id)
            attempt = await session.scalar(
                select(ConnectorSetupAttemptRecord)
                .where(ConnectorSetupAttemptRecord.connector_connection_id == connection.id)
                .order_by(ConnectorSetupAttemptRecord.generation.desc())
                .limit(1)
            )
        if attempt is None or attempt.external_ref is None:
            return False
        snapshot = await self._setup.attempt_snapshot(attempt.id, operation=SecretOperation.reconciliation)
        adapter = require_adapter(self._adapters, snapshot.connector.driver_key, snapshot.connector.config_version)
        try:
            inspection = await adapter.inspect_connection(
                endpoint=snapshot.connector.endpoint,
                connector_config=snapshot.connector.config_json,
                credentials=snapshot.credentials,
                external_ref=attempt.external_ref,
                expected_provider_key=attempt.provider_key,
                expected_external_user_correlation=attempt.external_user_correlation,
            )
        except ConnectorAdapterError:
            return False
        if inspection.status is not AdapterConnectionStatus.action_required:
            return False
        now = self._clock()
        async with transaction(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id, with_for_update=True)
            if (
                operation is None
                or operation.status != "unknown"
                or operation.claim_owner != self._instance_id
                or operation.claim_generation != claim_generation
            ):
                return True
            connection = await require_connection(session, operation.connector_connection_id, lock=True)
            verify_inspection(attempt, connection, inspection)
            apply_inspection(connection, inspection, now=now)
            operation.status = "succeeded"
            operation.completed_at = now
            operation.updated_at = now
        return True

    async def _catalog_candidate(self) -> str | None:
        now = self._clock()
        async with short_session(self._sessions) as session:
            return await session.scalar(
                select(ConnectorConnectionRecord.id)
                .where(
                    ConnectorConnectionRecord.status == "ready",
                    ConnectorConnectionRecord.deleted_at.is_(None),
                    ConnectorConnectionRecord.catalog_available_at <= now,
                )
                .order_by(ConnectorConnectionRecord.catalog_available_at, ConnectorConnectionRecord.id)
                .limit(1)
            )

    async def _record_catalog_failure(self, connection_id: str) -> None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            connection.catalog_attempt_count += 1
            connection.catalog_last_error_code = "catalog_refresh_failed"
            connection.catalog_available_at = self._clock() + timedelta(seconds=30)
            if connection.catalog_claim_owner == self._instance_id:
                connection.catalog_claim_owner = None
                connection.catalog_claim_expires_at = None

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
            if attempt is None or not _owns_attempt(attempt, self._instance_id, claim_generation):
                return
            attempt.available_at = self._clock() + timedelta(seconds=5)
            if increment:
                attempt.attempt_count += 1
            attempt.last_error_code = code

    async def _release_attempt(self, attempt_id: str, claim_generation: int) -> None:
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or not _owns_attempt(attempt, self._instance_id, claim_generation):
                return
            attempt.claim_owner = None
            attempt.claim_expires_at = None


def _owns_attempt(
    attempt: ConnectorSetupAttemptRecord | None,
    owner: str,
    generation: int,
) -> bool:
    return attempt is not None and attempt.claim_owner == owner and attempt.claim_generation == generation
