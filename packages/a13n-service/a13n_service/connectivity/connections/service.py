"""Connection management independent of the credential protocol."""

from __future__ import annotations

from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import record_command, replay_command
from a13n_service.durable_operations.entity_keys import entity_key, find_by_key
from a13n_service.durable_operations.idempotency import (
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from ..connectors.management import authorize_provider, connector_actor_scope, require_connector_provider
from ..connectors.models import ConnectorConnectionRecord
from ..mcp.credentials import MCPCredentialError, normalize_static_header_names
from ..mcp.models import MCPConnectionRecord
from .access import ConnectionError, audit, authorize, project, require_connection, require_version
from .domain import ConnectionCollection, CreateConnectionRequest, MCPSource, UpdateConnectionRequest
from .models import AuthorizationRecord, ConnectionRecord


class ConnectionService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], endpoint_policy: EndpointPolicy, *, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._endpoint_policy = endpoint_policy
        self._clock = clock

    async def create(
        self, *, actor: AuthenticatedActor, workspace_id: str, idempotency_key: str, request: CreateConnectionRequest
    ) -> Connection:
        key = idempotency_digest(idempotency_key)
        source = request.source
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, mode="manage")
            replay = await find_by_key(
                session,
                ConnectionRecord,
                entity_key(
                    actor,
                    operation="connection.create",
                    scope_id=workspace_id,
                    key_digest=key,
                    workspace_id=workspace_id,
                ),
            )
            if replay is not None:
                return replay.to_resource()
        if isinstance(source, MCPSource):
            try:
                endpoint = await self._endpoint_policy.validate(source.endpoint_url, resolve_dns=True)
                names = normalize_static_header_names(source.static_header_names)
                if bool(names) != (source.auth_mode == "static_headers"):
                    raise ValueError("Static header names must match the authentication mode")
                source = source.model_copy(update={"endpoint_url": endpoint, "static_header_names": names})
            except (EndpointPolicyError, MCPCredentialError, ValueError) as error:
                raise ConnectionError(
                    "invalid_source", "Connection source is invalid.", category=ErrorCategory.invalid_request
                ) from error
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, mode="manage")
                replay = await find_by_key(
                    session,
                    ConnectionRecord,
                    entity_key(
                        actor,
                        operation="connection.create",
                        scope_id=workspace_id,
                        key_digest=key,
                        workspace_id=workspace_id,
                    ),
                )
                if replay is not None:
                    return replay.to_resource()
                values = dict(
                    id=new_object_id("conn"),
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    status="pending",
                    status_reason=None,
                    version=1,
                    authorization_generation=1,
                    setup_generation=1,
                    credential_generation=0,
                    safe_metadata_json={},
                    static_header_names_json=[],
                    refresh_claim_generation=0,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                if isinstance(source, MCPSource):
                    values["static_header_names_json"] = list(source.static_header_names)
                    record = MCPConnectionRecord(**values, endpoint_url=source.endpoint_url, auth_mode=source.auth_mode)
                    record.request_key = entity_key(
                        actor,
                        operation="connection.create",
                        scope_id=workspace_id,
                        key_digest=key,
                        workspace_id=workspace_id,
                    )
                else:
                    provider = await require_connector_provider(
                        session, source.provider_id, scope=await connector_actor_scope(session, actor)
                    )
                    await authorize_provider(session, actor, provider)
                    if provider.organization_id != workspace.organization_id or (
                        provider.workspace_id is not None and provider.workspace_id != workspace_id
                    ):
                        raise ConnectionError(
                            "resource_not_found",
                            "The requested resource was not found.",
                            category=ErrorCategory.not_found,
                        )
                    record = ConnectorConnectionRecord(
                        **values, connector_provider_id=provider.id, connector_key=source.connector_key
                    )
                    record.request_key = entity_key(
                        actor,
                        operation="connection.create",
                        scope_id=workspace_id,
                        key_digest=key,
                        workspace_id=workspace_id,
                    )
                session.add(record)
                await session.flush()
                resource = project(record)
                session.add(audit(actor, record, action="connection.create", now=now))
                return resource
        except IntegrityError as error:
            async with transaction(self._sessions) as session:
                await authorize(session, actor, workspace_id, mode="manage")
                replay = await find_by_key(
                    session,
                    ConnectionRecord,
                    entity_key(
                        actor,
                        operation="connection.create",
                        scope_id=workspace_id,
                        key_digest=key,
                        workspace_id=workspace_id,
                    ),
                )
                if replay is not None:
                    return replay.to_resource()
            raise ConnectionError(
                "connection_conflict", "Connection name already exists.", category=ErrorCategory.conflict
            ) from error

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> Connection:
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id)
            await authorize(session, actor, record.workspace_id, mode="read")
            return project(record)

    async def list(
        self, *, actor: AuthenticatedActor, workspace_id: str, limit: int, cursor: str | None
    ) -> ConnectionCollection:
        if not 1 <= limit <= 100:
            raise ConnectionError(
                "invalid_request", "Collection limit is invalid.", category=ErrorCategory.invalid_request
            )
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="conn") if cursor else None
        except CursorError as error:
            raise ConnectionError(
                "invalid_cursor", "Collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, mode="read")
            query = select(ConnectionRecord).where(
                ConnectionRecord.workspace_id == workspace_id, ConnectionRecord.deleted_at.is_(None)
            )
            if position is not None:
                query = query.where(
                    or_(
                        ConnectionRecord.updated_at < position[0],
                        and_(ConnectionRecord.updated_at == position[0], ConnectionRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ConnectionRecord.updated_at.desc(), ConnectionRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = (
                encode_cursor(updated_at=page[-1].updated_at, object_id=page[-1].id, scope=scope)
                if len(records) > limit
                else None
            )
            return ConnectionCollection(items=tuple(project(record) for record in page), next_cursor=next_cursor)

    async def update(
        self, *, actor: AuthenticatedActor, connection_id: str, request: UpdateConnectionRequest
    ) -> Connection:
        try:
            async with transaction(self._sessions) as session:
                record = await require_connection(session, connection_id, lock=True)
                await authorize(session, actor, record.workspace_id, mode="manage")
                require_version(record, request.expected_version)
                record.name, record.normalized_name = request.name, request.name.casefold()
                record.version += 1
                record.updated_at = self._clock()
                session.add(audit(actor, record, action="connection.update", now=self._clock()))
                return project(record)
        except IntegrityError as error:
            raise ConnectionError(
                "connection_conflict", "Connection name already exists.", category=ErrorCategory.conflict
            ) from error

    async def set_enabled(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        enabled: bool,
        idempotency_key: str,
    ) -> Connection:
        operation = "connection.enable" if enabled else "connection.disable"
        key = idempotency_digest(idempotency_key)
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            await authorize(session, actor, record.workspace_id, mode="manage")
            replay = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=record.id,
                idempotency_key_digest=key,
                now=self._clock(),
            )
            if replay is not None:
                return record.to_resource()
            require_version(record, expected_version)
            record.status = "pending" if enabled else "disabled"
            record.status_reason = None
            record.version += 1
            record.updated_at = self._clock()
            record.refresh_claim_generation += 1
            record.refresh_claim_owner = None
            record.refresh_claim_expires_at = None
            if not enabled:
                await self.cancel_pending(session, record)
            resource = project(record)
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=record.id,
                idempotency_key_digest=key,
                resource_type="connection",
                resource_id=record.id,
                now=self._clock(),
            )
            session.add(audit(actor, record, action=operation, now=self._clock()))
            return resource

    async def cancel_pending(self, session: AsyncSession, record: ConnectionRecord) -> None:
        attempts = await session.scalars(
            select(AuthorizationRecord)
            .where(
                AuthorizationRecord.connection_id == record.id,
                AuthorizationRecord.status.not_in(("completed", "failed", "expired", "cancelled")),
            )
            .with_for_update()
        )
        for attempt in attempts:
            attempt.status = "cancelled"
            attempt.clear_credential()
            attempt.claim_owner = None
            attempt.claim_expires_at = None
            attempt.updated_at = self._clock()


def idempotency_digest(value: str) -> str:
    try:
        return digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise ConnectionError(
            "invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request
        ) from error
