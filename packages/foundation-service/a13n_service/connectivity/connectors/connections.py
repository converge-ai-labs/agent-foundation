"""Short-transaction ConnectorConnection lifecycle orchestration."""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
    canonical_digest,
    fingerprint,
    record_command,
    replay_command,
)
from a13n_service.iam import AuthenticatedActor, PrincipalType
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .connection_access import (
    authorize_connection,
    connection_resource,
    idempotency_digest,
    replay_connection_command,
    require_version,
)
from .domain import (
    ConnectorConnection,
    ConnectorConnectionCollection,
    ConnectorConnectionStatus,
    ConnectorSetupLaunch,
    CreateConnectorConnectionRequest,
    UpdateConnectorConnectionRequest,
)
from .errors import ConnectorError
from .management import (
    audit,
    authorize,
    authorize_provider,
    connector_actor_scope,
    map_management_value_error,
    require_connection,
    require_connector_provider,
    require_implementation,
)
from .models import (
    ConnectorConnectionRecord,
    ConnectorSetupAttemptRecord,
)
from .revocation import ConnectorRevocationService
from .setup import ConnectorSetupCoordinator


class ConnectorConnectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        protector: SecretProtector,
        *,
        correlation_secret: bytes | None,
        public_origin: str | None,
        setup_ttl_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._clock = clock
        self._setup = ConnectorSetupCoordinator(
            sessions,
            adapters,
            protector,
            correlation_secret=correlation_secret,
            public_origin=public_origin,
            setup_ttl_seconds=setup_ttl_seconds,
            clock=clock,
        )
        self._revocation = ConnectorRevocationService(sessions, adapters, protector, clock=clock)

    @property
    def setup_coordinator(self) -> ConnectorSetupCoordinator:
        return self._setup

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateConnectorConnectionRequest,
    ) -> ConnectorConnection:
        key_digest = idempotency_digest(idempotency_key)
        connection_id = new_object_id("cconn")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, WorkspaceAction.connector_connection_manage)
                connector = await require_connector_provider(
                    session, request.connector_provider_id, scope=await connector_actor_scope(session, actor)
                )
                await authorize_provider(session, actor, connector)
                if not workspace.contains(connector.organization_id, connector.workspace_id):
                    raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404)
                require_implementation(self._adapters, connector.type)
                request_fingerprint = fingerprint(request)
                try:
                    replay = await replay_command(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        operation="connector_connection.create",
                        scope_id=workspace_id,
                        idempotency_key_digest=key_digest,
                        fingerprint=request_fingerprint,
                    )
                except ConnectivityManagementValueError as error:
                    raise map_management_value_error(error) from error
                if replay is not None:
                    return await connection_resource(session, replay.resource_id)
                connection = ConnectorConnectionRecord(
                    id=connection_id,
                    organization_id=connector.organization_id,
                    workspace_id=workspace_id,
                    connector_provider_id=connector.id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    connector_key=request.connector_key,
                    external_ref=None,
                    safe_metadata_json={},
                    status=ConnectorConnectionStatus.pending.value,
                    status_reason=None,
                    version=1,
                    setup_generation=1,
                    deleted_at=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(connection)
                record_command(
                    session,
                    actor=actor,
                    organization_id=connector.organization_id,
                    workspace_id=workspace_id,
                    operation="connector_connection.create",
                    scope_id=workspace_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_connection",
                    resource_id=connection.id,
                    result_version=1,
                    now=now,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=connector.organization_id,
                        workspace_id=workspace_id,
                        action="connector_connection.create",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )
                await session.flush()
                return connection.to_resource()
        except IntegrityError as error:
            raise ConnectorError(
                "connector_connection_conflict",
                "ConnectorConnection identity or name already exists.",
                status_code=409,
            ) from error

    async def start_setup(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
        setup: JsonObject,
        return_path: str,
    ) -> ConnectorSetupLaunch:
        if actor.principal.principal_type is not PrincipalType.user:
            raise ConnectorError("interactive_user_required", "Interactive setup requires a User.", status_code=403)
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest(
            {"expected_version": expected_version, "setup": setup, "return_path": return_path}
        )
        attempt_id = new_object_id("csa")
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation="connector_connection.setup",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                attempt = await session.get(ConnectorSetupAttemptRecord, replay.resource_id)
                if attempt is None:
                    raise ConnectorError(
                        "setup_unavailable", "ConnectorProvider setup is unavailable.", status_code=404
                    )
                attempt_id = attempt.id
            else:
                require_version(connection.version, expected_version)
                connector = await require_connector_provider(
                    session,
                    connection.connector_provider_id,
                    scope=ResourceScope(connection.organization_id, connection.workspace_id),
                )
                if connector.status != "active":
                    raise ConnectorError("connector_disabled", "ConnectorProvider is disabled.", status_code=409)
                adapter = require_implementation(self._adapters, connector.type)
                try:
                    validated_setup = adapter.validate_setup(
                        setup,
                        connector_key=connection.connector_key,
                        configuration=connector.configuration_json,
                    )
                except ValueError as error:
                    raise ConnectorError(
                        "invalid_connector_setup", "ConnectorProvider setup is invalid.", status_code=400
                    ) from error
                session.add(
                    self._setup.new_attempt(
                        connection,
                        connector=connector,
                        actor=actor,
                        attempt_id=attempt_id,
                        setup=validated_setup,
                        return_path=return_path,
                        now=now,
                    )
                )
                record_command(
                    session,
                    actor=actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    operation="connector_connection.setup",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_setup_attempt",
                    resource_id=attempt_id,
                    result_version=connection.version,
                    now=now,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        action="connector_connection.setup",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )
        return await self._setup.launch(attempt_id, connection_id)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> ConnectorConnectionCollection:
        if not 1 <= limit <= 100:
            raise ConnectorError("invalid_request", "Collection limit is invalid.", status_code=400)
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="cconn") if cursor else None
        except CursorError as error:
            raise ConnectorError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.connector_connection_read)
            query = select(ConnectorConnectionRecord).where(
                ConnectorConnectionRecord.workspace_id == workspace_id,
                ConnectorConnectionRecord.deleted_at.is_(None),
            )
            if position is not None:
                query = query.where(
                    or_(
                        ConnectorConnectionRecord.updated_at < position[0],
                        and_(
                            ConnectorConnectionRecord.updated_at == position[0],
                            ConnectorConnectionRecord.id < position[1],
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            ConnectorConnectionRecord.updated_at.desc(),
                            ConnectorConnectionRecord.id.desc(),
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            items = tuple(record.to_resource() for record in page)
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return ConnectorConnectionCollection(items=items, next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> ConnectorConnection:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize_connection(session, actor, record, mode="read")
            return await connection_resource(session, connection_id)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        request: UpdateConnectorConnectionRequest,
    ) -> ConnectorConnection:
        try:
            async with transaction(self._sessions) as session:
                record = await require_connection(session, connection_id, lock=True)
                await authorize_connection(session, actor, record, mode="manage")
                require_version(record.version, request.expected_version)
                if request.name is not None:
                    record.name = request.name
                    record.normalized_name = request.name.casefold()
                record.version += 1
                record.updated_at = self._clock()
                session.add(
                    audit(
                        actor,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        action="connector_connection.update",
                        resource_type="connector_connection",
                        resource_id=record.id,
                        now=record.updated_at,
                    )
                )
                return record.to_resource()
        except IntegrityError as error:
            raise ConnectorError(
                "connector_connection_conflict",
                "ConnectorConnection name already exists.",
                status_code=409,
            ) from error

    async def set_enabled(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        enabled: bool,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectorConnection:
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version, "enabled": enabled})
        operation = "connector_connection.enable" if enabled else "connector_connection.disable"
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            await authorize_connection(
                session,
                actor,
                record,
                mode="manage",
            )
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=record,
                operation=operation,
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return record.to_resource()
            require_version(record.version, expected_version)
            completed_setup = (
                await session.scalar(
                    select(ConnectorSetupAttemptRecord.id).where(
                        ConnectorSetupAttemptRecord.connector_connection_id == record.id,
                        ConnectorSetupAttemptRecord.generation == record.setup_generation,
                        ConnectorSetupAttemptRecord.status == "completed",
                        ConnectorSetupAttemptRecord.external_ref == record.external_ref,
                    )
                )
                if enabled
                else None
            )
            if enabled and (record.external_ref is None or completed_setup is None):
                raise ConnectorError(
                    "connection_not_ready",
                    "ConnectorConnection has no verified setup.",
                    status_code=409,
                )
            target = ConnectorConnectionStatus.ready.value if enabled else ConnectorConnectionStatus.disabled.value
            if record.status == target or (enabled and record.status != ConnectorConnectionStatus.disabled.value):
                result = record.to_resource()
            else:
                record.status = target
                record.status_reason = None
                record.version += 1
                record.updated_at = self._clock()
                result = record.to_resource()
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector_connection",
                resource_id=record.id,
                result_version=record.version,
                now=self._clock(),
            )
            session.add(
                audit(
                    actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action=operation,
                    resource_type="connector_connection",
                    resource_id=record.id,
                    now=record.updated_at,
                )
            )
            return result

    async def reconnect(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
        setup: JsonObject,
        return_path: str,
    ) -> ConnectorSetupLaunch:
        if actor.principal.principal_type is not PrincipalType.user:
            raise ConnectorError("interactive_user_required", "Interactive setup requires a User.", status_code=403)
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest(
            {"expected_version": expected_version, "setup": setup, "return_path": return_path}
        )
        attempt_id = new_object_id("csa")
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation="connector_connection.reconnect",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                attempt_id = replay.resource_id
            else:
                require_version(connection.version, expected_version)
                if connection.status not in {
                    ConnectorConnectionStatus.action_required.value,
                    ConnectorConnectionStatus.pending.value,
                    ConnectorConnectionStatus.disabled.value,
                }:
                    raise ConnectorError(
                        "invalid_connection_state", "ConnectorConnection cannot reconnect.", status_code=409
                    )
                connector = await require_connector_provider(
                    session,
                    connection.connector_provider_id,
                    scope=ResourceScope(connection.organization_id, connection.workspace_id),
                )
                adapter = require_implementation(self._adapters, connector.type)
                try:
                    validated_setup = adapter.validate_setup(
                        setup,
                        connector_key=connection.connector_key,
                        configuration=connector.configuration_json,
                    )
                except ValueError as error:
                    raise ConnectorError(
                        "invalid_connector_setup", "ConnectorProvider setup is invalid.", status_code=400
                    ) from error
                connection.setup_generation += 1
                connection.status = ConnectorConnectionStatus.pending.value
                connection.status_reason = None
                connection.version += 1
                connection.updated_at = now
                session.add(
                    self._setup.new_attempt(
                        connection,
                        connector=connector,
                        actor=actor,
                        attempt_id=attempt_id,
                        setup=validated_setup,
                        return_path=return_path,
                        now=now,
                    )
                )
                record_command(
                    session,
                    actor=actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    operation="connector_connection.reconnect",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_setup_attempt",
                    resource_id=attempt_id,
                    result_version=connection.version,
                    now=now,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        action="connector_connection.reconnect",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )
        return await self._setup.launch(attempt_id, connection_id)

    async def revoke(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectionCleanupReceipt:
        return await self._revocation.invalidate(
            delete=False,
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectionCleanupReceipt:
        return await self._revocation.invalidate(
            delete=True,
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    async def complete_callback(
        self,
        *,
        actor: AuthenticatedActor,
        session_uri: str,
    ) -> str:
        return await self._setup.complete_callback(actor=actor, session_uri=session_uri)
