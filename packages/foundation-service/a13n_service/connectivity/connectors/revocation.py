"""Durable ConnectorConnection revocation and tombstoning."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import ConnectorAdapter, ConnectorAdapterError
from a13n_service.connectivity.management import canonical_digest, record_command
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .connection_access import (
    authorize_connection,
    connection_resource,
    external_error,
    idempotency_digest,
    replay_connection_command,
    require_version,
)
from .domain import ConnectorConnectionStatus, ConnectorConnectionStatusReason, ConnectorOperationReceipt
from .errors import ConnectorError
from .management import (
    audit,
    decode_credentials,
    require_adapter,
    require_connection,
    require_connector,
    secret_context,
)
from .models import ConnectorOperationRecord


class ConnectorRevocationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[ConnectorAdapter],
        secrets: InternalSecretService,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._secrets = secrets
        self._clock = clock

    async def revoke(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectorOperationReceipt:
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        now = self._clock()
        operation_id = new_object_id("cop")
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="administrative")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation="connector_connection.revoke",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                operation_id = replay.resource_id
            else:
                require_version(connection.version, expected_version)
                if connection.external_ref is None:
                    raise ConnectorError(
                        "setup_incomplete", "ConnectorConnection setup is incomplete.", status_code=409
                    )
                connection.status = ConnectorConnectionStatus.disabled.value
                connection.status_reason = None
                connection.revoke_generation += 1
                connection.version += 1
                connection.updated_at = now
                session.add(_new_operation(connection, operation_id=operation_id, now=now))
                record_command(
                    session,
                    actor=actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    operation="connector_connection.revoke",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_connection_operation",
                    resource_id=operation_id,
                    result_version=connection.version,
                    now=now,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        action="connector_connection.revoke",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )
        try:
            await self.run(operation_id)
        except ConnectorError:
            pass
        async with short_session(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id)
            if operation is None:
                raise ConnectorError("operation_unavailable", "Connector operation is unavailable.", status_code=404)
            return ConnectorOperationReceipt.model_validate(
                {
                    "operation_id": operation_id,
                    "status": operation.status,
                    "connection": await connection_resource(session, connection_id),
                }
            )

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> None:
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="administrative")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation="connector_connection.delete",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return
            require_version(connection.version, expected_version)
            if connection.external_ref is not None and not await _has_confirmed_revoke(session, connection.id):
                raise ConnectorError(
                    "revocation_required",
                    "ConnectorConnection must be revoked before deletion.",
                    status_code=409,
                )
            deleted_at = self._clock()
            connection.deleted_at = deleted_at
            connection.version += 1
            connection.updated_at = deleted_at
            record_command(
                session,
                actor=actor,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                operation="connector_connection.delete",
                scope_id=connection.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector_connection",
                resource_id=connection.id,
                result_version=connection.version,
                now=deleted_at,
            )
            session.add(
                audit(
                    actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    action="connector_connection.delete",
                    resource_type="connector_connection",
                    resource_id=connection.id,
                    now=deleted_at,
                )
            )

    async def run(
        self,
        operation_id: str,
        *,
        claim_owner: str | None = None,
        claim_generation: int | None = None,
    ) -> None:
        async with short_session(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id)
            if operation is None:
                return
            connection = await require_connection(session, operation.connector_connection_id)
            connector = await require_connector(session, connection.connector_id)
            if connection.external_ref is None:
                raise ConnectorError("setup_incomplete", "ConnectorConnection setup is incomplete.", status_code=409)
            external_ref = connection.external_ref
        try:
            raw = await self._secrets.resolve(secret_context(connector, operation=SecretOperation.reconciliation))
            adapter = require_adapter(self._adapters, connector.driver_key, connector.config_version)
            await adapter.revoke_connection(
                endpoint=connector.endpoint,
                connector_config=connector.config_json,
                credentials=decode_credentials(raw),
                external_ref=external_ref,
                operation_id=operation_id,
            )
        except (ConnectorAdapterError, InternalSecretError) as error:
            await self._record_failure(
                operation_id,
                error,
                claim_owner=claim_owner,
                claim_generation=claim_generation,
            )
            if isinstance(error, ConnectorAdapterError):
                raise external_error(error) from error
            raise ConnectorError(
                "credential_unavailable", "Connector credentials are unavailable.", status_code=503
            ) from error
        await self._record_success(
            operation_id,
            claim_owner=claim_owner,
            claim_generation=claim_generation,
        )

    async def _record_failure(
        self,
        operation_id: str,
        error: ConnectorAdapterError | InternalSecretError,
        *,
        claim_owner: str | None,
        claim_generation: int | None,
    ) -> None:
        adapter_error = error if isinstance(error, ConnectorAdapterError) else None
        code = adapter_error.code if adapter_error is not None else "credential_unavailable"
        if adapter_error is not None and adapter_error.outcome_unknown:
            status = "unknown"
        elif adapter_error is not None and not adapter_error.retryable:
            status = "failed"
        else:
            status = "pending"
        async with transaction(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id, with_for_update=True)
            if (
                operation is not None
                and operation.status in {"pending", "unknown"}
                and _claim_matches(operation, claim_owner, claim_generation)
            ):
                operation.status = status
                operation.attempt_count += 1
                operation.last_error_code = code
                operation.updated_at = self._clock()
                operation.available_at = operation.updated_at + timedelta(
                    seconds=(adapter_error.retry_after_seconds if adapter_error is not None else None) or 5
                )
                if status == "failed":
                    operation.completed_at = operation.updated_at

    async def _record_success(
        self,
        operation_id: str,
        *,
        claim_owner: str | None,
        claim_generation: int | None,
    ) -> None:
        async with transaction(self._sessions) as session:
            operation = await session.get(ConnectorOperationRecord, operation_id, with_for_update=True)
            if (
                operation is None
                or operation.status not in {"pending", "unknown"}
                or not _claim_matches(operation, claim_owner, claim_generation)
            ):
                return
            connection = await require_connection(session, operation.connector_connection_id, lock=True)
            completed_at = self._clock()
            operation.status = "succeeded"
            operation.completed_at = completed_at
            operation.updated_at = completed_at
            connection.status = ConnectorConnectionStatus.action_required.value
            connection.status_reason = ConnectorConnectionStatusReason.reauthorization_required.value
            connection.version += 1
            connection.updated_at = completed_at


def _new_operation(connection, *, operation_id: str, now: datetime) -> ConnectorOperationRecord:
    return ConnectorOperationRecord(
        id=operation_id,
        organization_id=connection.organization_id,
        workspace_id=connection.workspace_id,
        connector_connection_id=connection.id,
        kind="revoke",
        generation=connection.revoke_generation,
        status="pending",
        available_at=now,
        attempt_count=0,
        claim_generation=0,
        claim_owner=None,
        claim_expires_at=None,
        last_error_code=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


async def _has_confirmed_revoke(session: AsyncSession, connection_id: str) -> bool:
    operation = await session.scalar(
        select(ConnectorOperationRecord.id)
        .where(
            ConnectorOperationRecord.connector_connection_id == connection_id,
            ConnectorOperationRecord.kind == "revoke",
            ConnectorOperationRecord.status == "succeeded",
        )
        .order_by(ConnectorOperationRecord.generation.desc())
        .limit(1)
    )
    return operation is not None


def _claim_matches(
    operation: ConnectorOperationRecord,
    claim_owner: str | None,
    claim_generation: int | None,
) -> bool:
    if claim_owner is None or claim_generation is None:
        return claim_owner is None and claim_generation is None and operation.claim_owner is None
    return operation.claim_owner == claim_owner and operation.claim_generation == claim_generation
