"""Short-transaction Connection management."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

import anyio
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.connectivity.management import CommandReceipt, fingerprint, record_command, replay_command
from a13n_service.credentials import CredentialSnapshot
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    IdempotencyConflict,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .credentials import (
    MCPCredentialError,
    bearer_bundle,
    static_header_bundle,
)
from .discovery import DiscoveryResult
from .domain import MCPAuthMode, MCPToolCollection, ReplaceMCPCredentialsRequest
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    invalidate_refresh_claim,
    map_management_error,
    require_connection,
    require_version,
)
from .models import MCPAuthorizationRecord, MCPConnectionOAuthClientRecord, MCPConnectionRecord

_RegistrationCleanupStatus = Literal["succeeded", "failed", "unknown"]


class ConnectionDiscovery(Protocol):
    async def discover(
        self, connection_id: str, *, actor: AuthenticatedActor, command_id: str | None = None
    ) -> DiscoveryResult: ...


class RegistrationCleaner(Protocol):
    async def cleanup_registration_bundle(self, bundle: dict[str, Any]) -> bool: ...


class MCPConnectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        endpoint_policy: EndpointPolicy,
        protector: SecretProtector,
        discovery: ConnectionDiscovery,
        *,
        registration_cleaner: RegistrationCleaner | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._endpoint_policy = endpoint_policy
        self._protector = protector
        self._discovery = discovery
        self._registration_cleaner = registration_cleaner
        self._clock = clock

    async def discover_tools(
        self, *, actor: AuthenticatedActor, connection_id: str, expected_version: int
    ) -> MCPToolCollection:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize_connection(session, actor, record, mode="manage")
            require_version(record.version, expected_version)
        result = await self._discovery.discover(connection_id, actor=actor)
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize_connection(session, actor, record, mode="manage")
            require_version(record.version, expected_version)
        return MCPToolCollection(items=result.tools)

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        request: ReplaceMCPCredentialsRequest,
    ) -> Connection:
        key_digest = _idempotency_digest(idempotency_key)
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, record, mode="manage")
            credential_value = _credential_value(record, request)
            request_fingerprint = fingerprint(request, credentials={"bundle": credential_value})
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return _restore_connection(replay)
            require_version(record.version, request.expected_version)
            try:
                record.replace_credential(credential_value, self._protector)
                record.authorization_generation += 1
            except SecretProtectionError as error:
                raise MCPConnectionError(
                    "credential_conflict",
                    "MCP credentials could not be stored.",
                    category=ErrorCategory.conflict,
                ) from error

            record.status = "pending"
            record.status_reason = None
            record.version += 1
            record.updated_at = self._clock()
            invalidate_refresh_claim(record, now=record.updated_at)
            command_id = self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
                discovery_pending=True,
            )
        result = await self._discovery.discover(connection_id, actor=actor, command_id=command_id)
        return result.connection

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> ConnectionCleanupReceipt:
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = digest_request({"expected_version": expected_version})
        credentials = []
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True, include_deleted=True)
            await authorize_connection(session, actor, record, mode="manage")
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=record.workspace_id,
                    operation="mcp_connection.delete",
                    scope_id=connection_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    now=self._clock(),
                )
            except IdempotencyConflict as error:
                raise map_management_error(error) from error
            if replay is not None:
                return replay.restore(ConnectionCleanupReceipt)
            require_version(record.version, expected_version)
            if record.auth_mode == "oauth" and record.ciphertext is not None:
                credentials.append(record.credential_snapshot())
            record.clear_credential()
            client_configuration = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            if client_configuration is not None:
                if client_configuration.ciphertext is not None:
                    credentials.append(client_configuration.credential_snapshot())
                await session.delete(client_configuration)
            now = self._clock()
            record.status = "disabled"
            record.status_reason = None
            record.deleted_at = now
            record.version += 1
            record.updated_at = now
            invalidate_refresh_claim(record, now=now)
            oauth_sessions = await session.scalars(
                select(MCPAuthorizationRecord).where(
                    MCPAuthorizationRecord.connection_id == connection_id,
                    MCPAuthorizationRecord.status.in_(("pending", "received", "exchanging")),
                )
            )
            for oauth_session in oauth_sessions:
                if oauth_session.ciphertext is not None:
                    credentials.append(oauth_session.credential_snapshot())
                oauth_session.status = "expired"
                oauth_session.clear_credential()
                oauth_session.claim_owner = None
                oauth_session.claim_expires_at = None
                oauth_session.updated_at = now
            receipt = ConnectionCleanupReceipt(
                connection_id=connection_id,
                local_status="deleted",
                remote_status="unknown" if credentials else "not_required",
            )
            command = record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation="mcp_connection.delete",
                scope_id=connection_id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="mcp_connection",
                resource_id=connection_id,
                result_version=record.version,
                now=now,
                resource=receipt,
            )
            command_id = command.id
            session.add(audit(actor, record, action="mcp_connection.delete", now=now))
        if not credentials:
            return receipt
        outcome = await self._cleanup_registrations(credentials)
        receipt = ConnectionCleanupReceipt(connection_id=connection_id, local_status="deleted", remote_status=outcome)
        async with transaction(self._sessions) as session:
            command = await session.get(IdempotencyEvidenceRecord, command_id, with_for_update=True)
            if command is not None and command.receipt_json is not None:
                command.receipt_json = {
                    "version": command.receipt_json["version"],
                    "resource": receipt.model_dump(mode="json"),
                }
        return receipt

    async def _cleanup_registrations(self, credentials: list[CredentialSnapshot]) -> _RegistrationCleanupStatus:
        outcomes: list[_RegistrationCleanupStatus] = []
        # Each owned registration gets one bounded attempt; command replay uses
        # the stored aggregate receipt and cannot repeat these side effects.
        for credential in credentials:
            outcome: _RegistrationCleanupStatus = "unknown"
            try:
                bundle = json.loads(credential.decrypt(self._protector))
                if isinstance(bundle, dict) and self._registration_cleaner is not None:
                    with anyio.fail_after(30):
                        cleaned = await self._registration_cleaner.cleanup_registration_bundle(bundle)
                    outcome = "succeeded" if cleaned else "unknown"
            except (SecretProtectionError, ValueError):
                outcome = "failed"
            except Exception:
                outcome = "unknown"
            outcomes.append(outcome)
        return "unknown" if "unknown" in outcomes else "failed" if "failed" in outcomes else "succeeded"

    async def _replay(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        record: MCPConnectionRecord,
        operation: str,
        key_digest: str,
        request_fingerprint: str,
    ) -> CommandReceipt | None:
        try:
            result = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                now=self._clock(),
            )
        except IdempotencyConflict as error:
            raise map_management_error(error) from error
        return result

    def _record(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        record: MCPConnectionRecord,
        operation: str,
        key_digest: str,
        request_fingerprint: str,
        discovery_pending: bool = False,
    ) -> str:
        command = record_command(
            session,
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            operation=operation,
            scope_id=record.id,
            idempotency_key_digest=key_digest,
            fingerprint=request_fingerprint,
            resource_type="mcp_connection",
            resource_id=record.id,
            result_version=record.version,
            now=self._clock(),
            resource=None if discovery_pending else record.to_resource(),
        )
        session.add(audit(actor, record, action=operation, now=self._clock()))
        return command.id


def _restore_connection(receipt: CommandReceipt) -> Connection:
    if receipt.resource is None:
        raise MCPConnectionError(
            "mcp_discovery_incomplete",
            "Discovery has not completed for this command. Read the connection before starting a new command.",
            category=ErrorCategory.conflict,
        )
    return receipt.restore(Connection)


def _validate_auth_identity(auth_mode: MCPAuthMode, header_names: tuple[str, ...]) -> None:
    if auth_mode is MCPAuthMode.static_headers and not header_names:
        raise MCPConnectionError(
            "invalid_mcp_connection",
            "static_headers requires a non-empty header name set.",
            category=ErrorCategory.invalid_request,
        )
    if auth_mode is not MCPAuthMode.static_headers and header_names:
        raise MCPConnectionError(
            "invalid_mcp_connection",
            "Static header names require static_headers authentication.",
            category=ErrorCategory.invalid_request,
        )


def _credential_value(record: MCPConnectionRecord, request: ReplaceMCPCredentialsRequest) -> str:
    try:
        if (
            record.auth_mode == MCPAuthMode.bearer.value
            and request.bearer is not None
            and request.static_headers is None
        ):
            return bearer_bundle(request.bearer)
        if (
            record.auth_mode == MCPAuthMode.static_headers.value
            and request.static_headers is not None
            and request.bearer is None
        ):
            return static_header_bundle(
                request.static_headers,
                expected_names=tuple(record.static_header_names_json),
            )
    except MCPCredentialError as error:
        raise MCPConnectionError(
            "invalid_credentials", "MCP credentials are invalid.", category=ErrorCategory.invalid_request
        ) from error
    raise MCPConnectionError(
        "invalid_credentials",
        "Credentials do not match the Connection authentication mode.",
        category=ErrorCategory.invalid_request,
    )


def _idempotency_digest(value: str) -> str:
    try:
        return digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise map_management_error(error) from error
