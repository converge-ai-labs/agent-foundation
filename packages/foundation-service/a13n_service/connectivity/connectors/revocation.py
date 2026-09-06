"""Invalidate locally, then make one bounded remote revocation attempt."""

from contextlib import aclosing

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.management import record_command
from a13n_service.durable_operations.idempotency import digest_request
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
from .contracts import ConnectorProviderError
from .errors import ConnectorError
from .management import (
    ProviderSnapshot,
    audit,
    configure_provider,
    decode_credentials,
    require_connection,
    require_connector_provider,
)
from .models import ConnectorSetupAttemptRecord
from .registry import ConnectorProviderRegistry


class ConnectorRevocationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        protector: SecretProtector,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
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
            if connection.external_ref is not None:
                try:
                    binding = await connection_binding(session, connection)
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
                select(ConnectorSetupAttemptRecord).where(
                    ConnectorSetupAttemptRecord.connector_connection_id == connection_id,
                    ConnectorSetupAttemptRecord.status.in_(("pending", "attached", "reserved")),
                )
            )
            for attempt in attempts:
                attempt.status = "expired"
                attempt.claim_generation += 1
                attempt.claim_owner = None
                attempt.claim_expires_at = None
                attempt.updated_at = now
            receipt = ConnectionCleanupReceipt(
                connection_id=connection_id,
                local_status="deleted" if delete else "disabled",
                remote_status="unknown" if connection.external_ref is not None else "not_required",
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
            credentials = decode_credentials(credential.decrypt(self._protector))
            runtime = configure_provider(self._adapters, provider, credentials)
            with anyio.fail_after(30):
                async with aclosing(runtime), aclosing(runtime.connect(binding)) as connection_runtime:
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
