"""Short-transaction MCPConnection management."""

from __future__ import annotations

import json
from typing import Any, Protocol

import anyio
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import (
    CommandReceipt,
    canonical_digest,
    fingerprint,
    idempotency_key_digest,
    record_command,
    replay_command,
)
from a13n_service.durable_operations.idempotency import IdempotencyConflict, InvalidIdempotencyKey
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .credentials import (
    MCPCredentialError,
    bearer_bundle,
    normalize_static_header_names,
    static_header_bundle,
)
from .domain import (
    CreateMCPConnectionRequest,
    MCPAuthMode,
    MCPConnection,
    MCPConnectionCollection,
    MCPConnectionStatus,
    MCPTool,
    ReplaceMCPCredentialsRequest,
    UpdateMCPConnectionRequest,
)
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    authorize_workspace_action,
    invalidate_refresh_claim,
    map_management_error,
    require_connection,
    require_version,
)
from .models import MCPConnectionRecord, MCPOAuthSessionRecord


class ConnectionDiscovery(Protocol):
    async def discover(self, connection_id: str) -> tuple[MCPTool, ...]: ...


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

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateMCPConnectionRequest,
    ) -> MCPConnection:
        key_digest = _idempotency_digest(idempotency_key)
        try:
            endpoint, _, _ = self._endpoint_policy.validate_syntax(request.endpoint_url)
            header_names = normalize_static_header_names(request.static_header_names)
        except (EndpointPolicyError, MCPCredentialError) as error:
            raise MCPConnectionError(
                "invalid_mcp_connection",
                "MCPConnection configuration is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error
        _validate_auth_identity(request.auth_mode, header_names)
        request_fingerprint = fingerprint(
            request.model_copy(update={"endpoint_url": endpoint, "static_header_names": header_names})
        )
        async with transaction(self._sessions) as session:
            await authorize_workspace_action(session, actor, workspace_id, WorkspaceAction.mcp_connection_manage)
        try:
            endpoint = await self._endpoint_policy.validate(endpoint, resolve_dns=True)
        except EndpointPolicyError as error:
            raise MCPConnectionError(
                "invalid_mcp_connection",
                "MCPConnection configuration is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error
        now = self._clock()
        connection_id = new_object_id("mcpc")
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace_action(
                    session, actor, workspace_id, WorkspaceAction.mcp_connection_manage
                )
                try:
                    replay = await replay_command(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        operation="mcp_connection.create",
                        scope_id=workspace_id,
                        idempotency_key_digest=key_digest,
                        fingerprint=request_fingerprint,
                        now=self._clock(),
                    )
                except IdempotencyConflict as error:
                    raise map_management_error(error) from error
                if replay is not None:
                    return replay.restore(MCPConnection)
                record = MCPConnectionRecord(
                    id=connection_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    endpoint_url=endpoint,
                    auth_mode=request.auth_mode.value,
                    static_header_names_json=list(header_names),
                    status=MCPConnectionStatus.pending.value,
                    status_reason=None,
                    version=1,
                    credential_generation=0,
                    refresh_claim_generation=0,
                    refresh_claim_owner=None,
                    refresh_claim_expires_at=None,
                    deleted_at=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                record_command(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    operation="mcp_connection.create",
                    scope_id=workspace_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="mcp_connection",
                    resource_id=connection_id,
                    result_version=1,
                    now=now,
                    resource=record.to_resource(),
                )
                session.add(audit(actor, record, action="mcp_connection.create", now=now))
                await session.flush()
        except IntegrityError as error:
            raise MCPConnectionError(
                "mcp_connection_conflict",
                "MCPConnection identity or name already exists.",
                category=ErrorCategory.conflict,
            ) from error
        if request.auth_mode is MCPAuthMode.none:
            await self._discovery.discover(connection_id)
        return record.to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> MCPConnectionCollection:
        if not 1 <= limit <= 100:
            raise MCPConnectionError(
                "invalid_request", "Collection limit is invalid.", category=ErrorCategory.invalid_request
            )
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="mcpc") if cursor else None
        except CursorError as error:
            raise MCPConnectionError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            await authorize_workspace_action(session, actor, workspace_id, WorkspaceAction.mcp_connection_read)
            query = select(MCPConnectionRecord).where(
                MCPConnectionRecord.workspace_id == workspace_id,
                MCPConnectionRecord.deleted_at.is_(None),
            )
            if position is not None:
                query = query.where(
                    or_(
                        MCPConnectionRecord.updated_at < position[0],
                        and_(MCPConnectionRecord.updated_at == position[0], MCPConnectionRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(MCPConnectionRecord.updated_at.desc(), MCPConnectionRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
            page = records[:limit]
            items = tuple(record.to_resource() for record in page)
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return MCPConnectionCollection(items=items, next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPConnection:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize_connection(session, actor, record, mode="read")
            return record.to_resource()

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        request: UpdateMCPConnectionRequest,
    ) -> MCPConnection:
        try:
            async with transaction(self._sessions) as session:
                record = await require_connection(session, connection_id, lock=True)
                await authorize_connection(session, actor, record, mode="manage")
                require_version(record.version, request.expected_version)
                record.name = request.name
                record.normalized_name = request.name.casefold()
                record.version += 1
                record.updated_at = self._clock()
                session.add(audit(actor, record, action="mcp_connection.update", now=record.updated_at))
                return record.to_resource()
        except IntegrityError as error:
            raise MCPConnectionError(
                "mcp_connection_conflict",
                "MCPConnection name already exists.",
                category=ErrorCategory.conflict,
            ) from error

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        request: ReplaceMCPCredentialsRequest,
    ) -> MCPConnection:
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
                return replay.restore(MCPConnection)
            require_version(record.version, request.expected_version)
            try:
                record.replace_credential(credential_value, self._protector)
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
            self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        await self._discovery.discover(connection_id)
        return record.to_resource()

    async def reconnect(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> MCPConnection:
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, record, mode="manage")
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.reconnect",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return replay.restore(MCPConnection)
            require_version(record.version, expected_version)
            if record.auth_mode != "none" and record.ciphertext is None:
                raise MCPConnectionError(
                    "credentials_required", "MCP credentials are required.", category=ErrorCategory.conflict
                )
            record.status = "pending"
            record.status_reason = None
            record.version += 1
            record.updated_at = self._clock()
            invalidate_refresh_claim(record, now=record.updated_at)
            self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.reconnect",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        await self._discovery.discover(connection_id)
        return record.to_resource()

    async def set_enabled(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
        enabled: bool,
    ) -> MCPConnection:
        operation = f"mcp_connection.{'enable' if enabled else 'disable'}"
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            await authorize_connection(
                session,
                actor,
                record,
                mode="manage",
            )
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation=operation,
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay.restore(MCPConnection)
            if not replay:
                require_version(record.version, expected_version)
                if enabled:
                    if record.auth_mode != "none" and record.ciphertext is None:
                        raise MCPConnectionError(
                            "connection_not_ready",
                            "MCPConnection has no eligible credentials.",
                            category=ErrorCategory.conflict,
                        )
                    record.status = "ready"
                else:
                    record.status = "disabled"
                    invalidate_refresh_claim(record, now=self._clock())
                record.status_reason = None
                record.version += 1
                record.updated_at = self._clock()
                self._record(
                    session,
                    actor=actor,
                    record=record,
                    operation=operation,
                    key_digest=key_digest,
                    request_fingerprint=request_fingerprint,
                )
            return record.to_resource()

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> ConnectionCleanupReceipt:
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
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
            now = self._clock()
            record.status = "disabled"
            record.status_reason = None
            record.deleted_at = now
            record.version += 1
            record.updated_at = now
            invalidate_refresh_claim(record, now=now)
            oauth_sessions = await session.scalars(
                select(MCPOAuthSessionRecord).where(
                    MCPOAuthSessionRecord.mcp_connection_id == connection_id,
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
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
        outcomes = []
        # Each owned registration gets one bounded attempt; command replay uses
        # the stored aggregate receipt and cannot repeat these side effects.
        for credential in credentials:
            outcome = "unknown"
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
        outcome = "unknown" if "unknown" in outcomes else "failed" if "failed" in outcomes else "succeeded"
        receipt = ConnectionCleanupReceipt(connection_id=connection_id, local_status="deleted", remote_status=outcome)
        async with transaction(self._sessions) as session:
            command = await session.get(IdempotencyEvidenceRecord, command_id, with_for_update=True)
            if command is not None and command.receipt_json is not None:
                command.receipt_json = {
                    "version": command.receipt_json["version"],
                    "resource": receipt.model_dump(mode="json"),
                }
        return receipt

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
    ) -> None:
        record_command(
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
            resource=record.to_resource(),
        )
        session.add(audit(actor, record, action=operation, now=self._clock()))


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
        "Credentials do not match the MCPConnection authentication mode.",
        category=ErrorCategory.invalid_request,
    )


def _idempotency_digest(value: str) -> str:
    try:
        return idempotency_key_digest(value)
    except InvalidIdempotencyKey as error:
        raise map_management_error(error) from error
