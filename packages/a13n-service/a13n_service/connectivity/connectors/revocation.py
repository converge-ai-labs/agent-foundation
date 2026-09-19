"""Invalidate locally, then make one bounded remote revocation attempt."""

import anyio
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector import ConnectorHttpClient, ConnectorProviderDefinition
from a13n_harness.providers.connector.contracts import ConnectionBinding, ConnectorProviderError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.management import record_command
from a13n_service.digests import digest_request
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .connection_access import (
    authorize_connection,
    connection_binding,
    idempotency_digest,
    replay_connection_command,
    require_version,
)
from .errors import ConnectorError
from .management import (
    ProviderSnapshot,
    audit,
    decode_credentials,
    open_provider,
    require_connection,
    require_connector_provider,
)
from .models import ConnectorAuthorizationRecord


class ConnectorRevocationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        connectors: ProviderCatalog[ConnectorProviderDefinition],
        connector_http: ConnectorHttpClient | None,
        protector: SecretProtector,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._connectors = connectors
        self._connector_http = connector_http
        self._protector = protector
        self._clock = clock

    async def invalidate(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
        delete: bool,
    ) -> ConnectionCleanupReceipt:
        key = idempotency_digest(idempotency_key)
        digest = digest_request({"expected_version": expected_version})
        operation = "connector_connection.delete" if delete else "connector_connection.revoke"
        binding = None
        credential = None
        provider = None
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True, include_deleted=True)
            await authorize_connection(session, actor, connection, mode="manage")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation=operation,
                key_digest=key,
                request_fingerprint=digest,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConnectionCleanupReceipt)
            require_version(connection.version, expected_version)
            try:
                if connection.external_ref is not None and connection.external_user_correlation is not None:
                    binding = connection_binding(connection)
                else:
                    # A temporary account remains attempt evidence until verified completion.
                    attempt = await session.scalar(
                        select(ConnectorAuthorizationRecord).where(
                            ConnectorAuthorizationRecord.connection_id == connection.id,
                            ConnectorAuthorizationRecord.generation == connection.setup_generation,
                        )
                    )
                    if attempt is not None and attempt.external_ref is not None:
                        if connection.external_ref is not None and connection.external_ref != attempt.external_ref:
                            raise ConnectorError(
                                "connection_substitution",
                                "Setup cleanup account differs.",
                                category=ErrorCategory.conflict,
                            )
                        binding = ConnectionBinding(
                            external_ref=attempt.external_ref,
                            connector_key=connection.connector_key,
                            external_user_correlation=attempt.external_user_correlation,
                        )
                if binding is not None:
                    connector = await require_connector_provider(
                        session,
                        connection.connector_provider_id,
                        scope=ResourceScope(connection.organization_id, connection.workspace_id),
                    )
                    credential = connector.credential_snapshot()
                    provider = ProviderSnapshot.from_record(connector)
            except (ConnectorError, SecretProtectionError):
                # Missing remote prerequisites must never prevent local invalidation.
                pass
            now = self._clock()
            connection.status = "disabled"
            connection.status_reason = None
            connection.setup_generation += 1
            connection.version += 1
            connection.updated_at = now
            if delete:
                connection.deleted_at = now
            attempts = await session.scalars(
                select(ConnectorAuthorizationRecord).where(
                    ConnectorAuthorizationRecord.connection_id == connection_id,
                    (
                        ConnectorAuthorizationRecord.status.in_(("pending", "starting", "attached", "reserved"))
                        | (ConnectorAuthorizationRecord.last_error_code == "setup_outcome_unknown")
                    ),
                )
            )
            setup_attempts = tuple(attempts)
            remote_setup_exists = any(
                attempt.setup_ref is not None
                or attempt.status == "starting"
                or attempt.last_error_code == "setup_outcome_unknown"
                for attempt in setup_attempts
            )
            for attempt in setup_attempts:
                if attempt.status in {"failed", "expired"}:
                    continue
                if attempt.status == "starting":
                    attempt.last_error_code = "setup_outcome_unknown"
                attempt.status = "expired"
                attempt.claim_generation += 1
                attempt.claim_owner = None
                attempt.claim_expires_at = None
                attempt.updated_at = now
            receipt = ConnectionCleanupReceipt(
                connection_id=connection_id,
                local_status="deleted" if delete else "disabled",
                remote_status="unknown"
                if connection.external_ref is not None or remote_setup_exists
                else "not_required",
            )
            command = record_command(
                session,
                actor=actor,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                operation=operation,
                scope_id=connection_id,
                idempotency_key_digest=key,
                fingerprint=digest,
                resource_type="connector_connection",
                resource_id=connection_id,
                result_version=connection.version,
                now=now,
                resource=receipt,
            )
            command_id = command.id
            session.add(
                audit(
                    actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    action=operation,
                    resource_type="connector_connection",
                    resource_id=connection_id,
                    now=now,
                )
            )
        if binding is None or credential is None or provider is None:
            return receipt
        # A crash after local commit leaves an honest unknown receipt, never a job
        # that might revoke a replacement authorization on a later retry.
        outcome = "unknown"
        try:
            credentials = decode_credentials(
                credential.decrypt(self._protector) if credential.ciphertext is not None else None
            )
            runtime = open_provider(self._connectors, self._connector_http, provider, credentials)
            with anyio.fail_after(30):
                async with runtime as provider_runtime:
                    connection_runtime = provider_runtime.connect(binding)
                    await connection_runtime.revoke(operation_id=command_id)
            outcome = "succeeded"
        except ConnectorProviderError as error:
            outcome = "unknown" if error.outcome_unknown else "failed"
        except SecretProtectionError:
            outcome = "failed"
        except Exception:
            outcome = "unknown"
        receipt = ConnectionCleanupReceipt(
            connection_id=connection_id, local_status="deleted" if delete else "disabled", remote_status=outcome
        )
        async with transaction(self._sessions) as session:
            command = await session.get(IdempotencyEvidenceRecord, command_id, with_for_update=True)
            if command is not None and command.receipt_json is not None:
                command.receipt_json = {
                    "version": command.receipt_json["version"],
                    "resource": receipt.model_dump(mode="json"),
                }
        return receipt
