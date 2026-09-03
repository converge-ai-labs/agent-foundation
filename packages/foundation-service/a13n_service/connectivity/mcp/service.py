"""Short-transaction MCPConnection management."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
    canonical_digest,
    fingerprint,
    idempotency_key_digest,
    record_command,
    replay_command,
)
from a13n_service.connectivity.outbound_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import AuthenticatedActor, PrincipalType
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import transaction

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
    ReplaceMCPCredentialsRequest,
    UpdateMCPConnectionRequest,
)
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    authorize_workspace_action,
    has_admin_access,
    invalidate_catalog_claim,
    map_management_error,
    not_found,
    require_connection,
    require_version,
    secret_context,
)
from .models import MCPConnectionRecord, MCPOAuthSessionRecord


class CatalogRefresher(Protocol):
    async def refresh(self, connection_id: str) -> str: ...


class RegistrationCleaner(Protocol):
    async def cleanup_registration_bundle(self, bundle: dict[str, Any]) -> bool: ...


class MCPConnectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        endpoint_policy: EndpointPolicy,
        secrets: InternalSecretService,
        catalog: CatalogRefresher,
        *,
        registration_cleaner: RegistrationCleaner | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._endpoint_policy = endpoint_policy
        self._secrets = secrets
        self._catalog = catalog
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
                status_code=400,
            ) from error
        _validate_auth_identity(request.auth_mode, header_names)
        request_fingerprint = fingerprint(
            request.model_copy(update={"endpoint_url": endpoint, "static_header_names": header_names})
        )
        async with transaction(self._sessions) as session:
            await _authorize_create(session, actor, workspace_id, request.owner_user_id)
        try:
            endpoint = await self._endpoint_policy.validate(endpoint, resolve_dns=True)
        except EndpointPolicyError as error:
            raise MCPConnectionError(
                "invalid_mcp_connection",
                "MCPConnection configuration is invalid.",
                status_code=400,
            ) from error
        now = self._clock()
        connection_id = new_object_id("mcpc")
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize_create(session, actor, workspace_id, request.owner_user_id)
                try:
                    replay = await replay_command(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        operation="mcp_connection.create",
                        scope_id=workspace_id,
                        idempotency_key_digest=key_digest,
                        fingerprint=request_fingerprint,
                    )
                except ConnectivityManagementValueError as error:
                    raise map_management_error(error) from error
                if replay is not None:
                    return await self._resource(session, replay.resource_id)
                record = MCPConnectionRecord(
                    id=connection_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    owner_user_id=request.owner_user_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    endpoint_url=endpoint,
                    auth_mode=request.auth_mode.value,
                    static_header_names_json=list(header_names),
                    status=MCPConnectionStatus.pending.value,
                    status_reason=None,
                    version=1,
                    credential_secret_id=None,
                    credential_generation=0,
                    catalog_generation=0,
                    current_catalog_digest=None,
                    catalog_claim_generation=0,
                    catalog_claim_owner=None,
                    catalog_claim_expires_at=None,
                    catalog_available_at=now,
                    catalog_last_error_code=None,
                    cleanup_pending=False,
                    cleanup_attempt_count=0,
                    cleanup_available_at=now,
                    cleanup_last_error_code=None,
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
                )
                session.add(audit(actor, record, action="mcp_connection.create", now=now))
                await session.flush()
        except IntegrityError as error:
            raise MCPConnectionError(
                "mcp_connection_conflict",
                "MCPConnection identity or name already exists.",
                status_code=409,
            ) from error
        if request.auth_mode is MCPAuthMode.none:
            await self._catalog.refresh(connection_id)
        return await self.get(actor=actor, connection_id=connection_id)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> MCPConnectionCollection:
        if not 1 <= limit <= 100:
            raise MCPConnectionError("invalid_request", "Collection limit is invalid.", status_code=400)
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="mcpc") if cursor else None
        except CursorError as error:
            raise MCPConnectionError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            await authorize_workspace_action(session, actor, workspace_id, WorkspaceAction.mcp_connection_read)
            admin_access = await has_admin_access(session, actor, workspace_id)
            query = select(MCPConnectionRecord).where(
                MCPConnectionRecord.workspace_id == workspace_id,
                MCPConnectionRecord.deleted_at.is_(None),
            )
            if not admin_access:
                query = query.where(_visible_owner(actor))
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
            items = tuple([await self._resource(session, record.id) for record in page])
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return MCPConnectionCollection(items=items, next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPConnection:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize_connection(session, actor, record, mode="read")
            return await self._resource(session, record.id)

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
                await authorize_connection(session, actor, record, mode="owner_manage")
                require_version(record.version, request.expected_version)
                record.name = request.name
                record.normalized_name = request.name.casefold()
                record.version += 1
                record.updated_at = self._clock()
                session.add(audit(actor, record, action="mcp_connection.update", now=record.updated_at))
                return await self._resource(session, record.id)
        except IntegrityError as error:
            raise MCPConnectionError(
                "mcp_connection_conflict",
                "MCPConnection name already exists.",
                status_code=409,
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
            await authorize_connection(session, actor, record, mode="owner_manage")
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
                return await self._resource(session, record.id)
            require_version(record.version, request.expected_version)
            try:
                if record.credential_secret_id is None:
                    secret_ref = await self._secrets.create_in_transaction(
                        session,
                        secret_context(
                            record,
                            operation=SecretOperation.management,
                            generation=1,
                        ),
                        credential_value,
                    )
                else:
                    secret_ref = await self._secrets.replace_in_transaction(
                        session,
                        secret_context(record, operation=SecretOperation.management),
                        credential_value,
                    )
            except InternalSecretError as error:
                raise MCPConnectionError(
                    "credential_conflict",
                    "MCP credentials could not be stored.",
                    status_code=409,
                ) from error
            record.credential_secret_id = secret_ref.secret_id
            record.credential_generation = secret_ref.version
            record.status = "pending"
            record.status_reason = None
            record.version += 1
            record.updated_at = self._clock()
            invalidate_catalog_claim(record, now=record.updated_at)
            self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        await self._catalog.refresh(connection_id)
        return await self.get(actor=actor, connection_id=connection_id)

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
            await authorize_connection(session, actor, record, mode="owner_manage")
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.reconnect",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return await self._resource(session, record.id)
            require_version(record.version, expected_version)
            if record.auth_mode != "none" and record.credential_secret_id is None:
                raise MCPConnectionError("credentials_required", "MCP credentials are required.", status_code=409)
            record.status = "pending"
            record.status_reason = None
            record.version += 1
            record.updated_at = self._clock()
            invalidate_catalog_claim(record, now=record.updated_at)
            self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.reconnect",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        await self._catalog.refresh(connection_id)
        return await self.get(actor=actor, connection_id=connection_id)

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
                mode="owner_manage" if enabled else "administrative",
            )
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation=operation,
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if not replay:
                require_version(record.version, expected_version)
                if enabled:
                    digest = record.current_catalog_digest
                    if digest is None or (record.auth_mode != "none" and record.credential_secret_id is None):
                        raise MCPConnectionError(
                            "connection_not_ready",
                            "MCPConnection has no compatible credentials and catalog.",
                            status_code=409,
                        )
                    record.status = "ready"
                else:
                    record.status = "disabled"
                    invalidate_catalog_claim(record, now=self._clock())
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
            return await self._resource(session, record.id)

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> None:
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        cleanup_required = False
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True, include_deleted=True)
            await authorize_connection(session, actor, record, mode="administrative")
            replay = await self._replay(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.delete",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return
            require_version(record.version, expected_version)
            cleanup_required = record.auth_mode == MCPAuthMode.oauth.value and record.credential_secret_id is not None
            if record.credential_secret_id is not None and not cleanup_required:
                await self._secrets.tombstone_in_transaction(
                    session,
                    secret_context(record, operation=SecretOperation.management),
                )
            deleted_at = self._clock()
            record.status = "disabled"
            record.status_reason = None
            record.cleanup_pending = cleanup_required
            record.cleanup_available_at = deleted_at
            record.deleted_at = deleted_at
            record.version += 1
            record.updated_at = deleted_at
            invalidate_catalog_claim(record, now=deleted_at)
            oauth_sessions = await session.scalars(
                select(MCPOAuthSessionRecord).where(
                    MCPOAuthSessionRecord.mcp_connection_id == record.id,
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
                )
            )
            for oauth_session in oauth_sessions:
                oauth_session.status = "expired"
                oauth_session.claim_owner = None
                oauth_session.claim_expires_at = None
                oauth_session.updated_at = deleted_at
            self._record(
                session,
                actor=actor,
                record=record,
                operation="mcp_connection.delete",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        if cleanup_required:
            await self.reconcile_cleanup(connection_id)

    async def reconcile_cleanup(self, connection_id: str) -> bool:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, include_deleted=True)
            if not record.cleanup_pending or record.credential_secret_id is None:
                return True
            generation = record.credential_generation
        cleaned = False
        try:
            raw = await self._secrets.resolve(
                secret_context(
                    record,
                    operation=SecretOperation.reconciliation,
                    generation=generation,
                )
            )
            bundle = json.loads(raw)
            if isinstance(bundle, dict) and self._registration_cleaner is not None:
                cleaned = await self._registration_cleaner.cleanup_registration_bundle(bundle)
        except (InternalSecretError, json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError):
            cleaned = False
        now = self._clock()
        async with transaction(self._sessions) as session:
            current = await require_connection(session, connection_id, lock=True, include_deleted=True)
            if not current.cleanup_pending or current.credential_generation != generation:
                return False
            current.cleanup_attempt_count += 1
            current.cleanup_available_at = now
            if not cleaned:
                current.cleanup_last_error_code = "registration_cleanup_failed"
                delay = min(3600, 30 * (2 ** min(current.cleanup_attempt_count - 1, 7)))
                current.cleanup_available_at = now + timedelta(seconds=delay)
                return False
            await self._secrets.tombstone_in_transaction(
                session,
                secret_context(current, operation=SecretOperation.reconciliation),
            )
            current.cleanup_pending = False
            current.cleanup_last_error_code = None
            return True

    async def _resource(self, session: AsyncSession, connection_id: str) -> MCPConnection:
        record = await require_connection(session, connection_id, include_deleted=True)
        return record.to_resource()

    async def _replay(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        record: MCPConnectionRecord,
        operation: str,
        key_digest: str,
        request_fingerprint: str,
    ) -> bool:
        try:
            result = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
            )
        except ConnectivityManagementValueError as error:
            raise map_management_error(error) from error
        return result is not None

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
            now=record.updated_at,
        )
        session.add(audit(actor, record, action=operation, now=record.updated_at))


def _validate_auth_identity(auth_mode: MCPAuthMode, header_names: tuple[str, ...]) -> None:
    if auth_mode is MCPAuthMode.static_headers and not header_names:
        raise MCPConnectionError(
            "invalid_mcp_connection",
            "static_headers requires a non-empty header name set.",
            status_code=400,
        )
    if auth_mode is not MCPAuthMode.static_headers and header_names:
        raise MCPConnectionError(
            "invalid_mcp_connection",
            "Static header names require static_headers authentication.",
            status_code=400,
        )


async def _authorize_create(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    owner_user_id: str | None,
):
    if owner_user_id is None:
        action = WorkspaceAction.mcp_connection_manage
    elif actor.principal.principal_type is PrincipalType.user and actor.principal.principal_id == owner_user_id:
        action = WorkspaceAction.mcp_connection_read
    else:
        raise not_found()
    return await authorize_workspace_action(session, actor, workspace_id, action)


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
        raise MCPConnectionError("invalid_credentials", "MCP credentials are invalid.", status_code=400) from error
    raise MCPConnectionError(
        "invalid_credentials",
        "Credentials do not match the MCPConnection authentication mode.",
        status_code=400,
    )


def _visible_owner(actor: AuthenticatedActor):
    if actor.principal.principal_type is PrincipalType.user:
        return or_(
            MCPConnectionRecord.owner_user_id.is_(None),
            MCPConnectionRecord.owner_user_id == actor.principal.principal_id,
        )
    return MCPConnectionRecord.owner_user_id.is_(None)


def _idempotency_digest(value: str) -> str:
    try:
        return idempotency_key_digest(value)
    except ConnectivityManagementValueError as error:
        raise map_management_error(error) from error
