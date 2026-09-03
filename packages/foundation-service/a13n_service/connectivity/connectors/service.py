"""Transactional Connector resource management."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import ConnectorAdapter, ConnectorAdapterError
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
    canonical_digest,
    canonical_json,
    clear_credentials,
    fingerprint,
    idempotency_key_digest,
    record_command,
    replay_command,
)
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import transaction

from .connection_access import external_error
from .domain import (
    Connector,
    ConnectorCollection,
    ConnectorStatus,
    ConnectorTestResult,
    CreateConnectorRequest,
    ReplaceConnectorCredentialsRequest,
    UpdateConnectorRequest,
)
from .errors import ConnectorError
from .management import (
    audit,
    authorize,
    decode_credentials,
    map_management_value_error,
    require_adapter,
    require_connector,
    secret_context,
)
from .models import ConnectorConnectionRecord, ConnectorRecord


class ConnectorService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[ConnectorAdapter],
        secrets: InternalSecretService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._secrets = secrets
        self._clock = clock

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateConnectorRequest,
    ) -> Connector:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except ConnectivityManagementValueError as error:
            raise map_management_value_error(error) from error
        connector_id = new_object_id("cnr")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, WorkspaceAction.connector_manage)
                adapter = require_adapter(self._adapters, request.driver_key, request.config_version)
                try:
                    config = adapter.validate_config(request.config, config_version=request.config_version)
                    endpoint = adapter.normalize_endpoint(request.endpoint, connector_config=config)
                    credentials = adapter.validate_credentials(
                        clear_credentials(request.credentials),
                        config_version=request.config_version,
                    )
                except ValueError as error:
                    raise ConnectorError(
                        "invalid_connector_configuration",
                        "Connector configuration is invalid.",
                        status_code=400,
                    ) from error
                request_fingerprint = fingerprint(request, credentials=credentials)
                try:
                    replay = await replay_command(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        operation="connector.create",
                        scope_id=workspace_id,
                        idempotency_key_digest=key_digest,
                        fingerprint=request_fingerprint,
                    )
                except ConnectivityManagementValueError as error:
                    raise map_management_value_error(error) from error
                if replay is not None:
                    return (await require_connector(session, replay.resource_id)).to_resource()
                record = ConnectorRecord(
                    id=connector_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    driver_key=request.driver_key,
                    config_version=request.config_version,
                    endpoint=endpoint,
                    config_json=config,
                    status=ConnectorStatus.active.value,
                    version=1,
                    credential_secret_id="",
                    credential_generation=1,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                secret_ref = await self._secrets.create_in_transaction(
                    session,
                    secret_context(record, operation=SecretOperation.management),
                    canonical_json(credentials),
                )
                record.credential_secret_id = secret_ref.secret_id
                record.credential_generation = secret_ref.version
                session.add(record)
                record_command(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    operation="connector.create",
                    scope_id=workspace_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector",
                    resource_id=connector_id,
                    result_version=1,
                    now=now,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        action="connector.create",
                        resource_type="connector",
                        resource_id=connector_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise ConnectorError(
                "connector_conflict",
                "Connector identity or name already exists.",
                status_code=409,
            ) from error
        except InternalSecretError as error:
            raise ConnectorError(
                "credential_conflict",
                "Connector credentials could not be stored.",
                status_code=409,
            ) from error

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> ConnectorCollection:
        if not 1 <= limit <= 100:
            raise ConnectorError("invalid_request", "Collection limit is invalid.", status_code=400)
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="cnr") if cursor else None
        except CursorError as error:
            raise ConnectorError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize(session, actor, workspace_id, WorkspaceAction.connector_read)
            query = select(ConnectorRecord).where(
                ConnectorRecord.organization_id == workspace.organization_id,
                ConnectorRecord.workspace_id == workspace_id,
            )
            if position is not None:
                query = query.where(
                    or_(
                        ConnectorRecord.updated_at < position[0],
                        and_(ConnectorRecord.updated_at == position[0], ConnectorRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ConnectorRecord.updated_at.desc(), ConnectorRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return ConnectorCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, connector_id: str) -> Connector:
        async with transaction(self._sessions) as session:
            record = await require_connector(session, connector_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.connector_read)
            return record.to_resource()

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        connector_id: str,
        request: UpdateConnectorRequest,
    ) -> Connector:
        try:
            async with transaction(self._sessions) as session:
                record = await require_connector(session, connector_id, lock=True)
                await authorize(session, actor, record.workspace_id, WorkspaceAction.connector_manage)
                _require_version(record.version, request.expected_version)
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
                        action="connector.update",
                        resource_type="connector",
                        resource_id=record.id,
                        now=record.updated_at,
                    )
                )
                return record.to_resource()
        except IntegrityError as error:
            raise ConnectorError("connector_conflict", "Connector name already exists.", status_code=409) from error

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connector_id: str,
        idempotency_key: str,
        request: ReplaceConnectorCredentialsRequest,
    ) -> Connector:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except ConnectivityManagementValueError as error:
            raise map_management_value_error(error) from error
        credentials = clear_credentials(request.credentials)
        request_fingerprint = fingerprint(request, credentials=credentials)
        async with transaction(self._sessions) as session:
            record = await require_connector(session, connector_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.connector_manage)
            replay = await _replay_connector_command(
                session,
                actor=actor,
                record=record,
                operation="connector.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return record.to_resource()
            _require_version(record.version, request.expected_version)
            adapter = require_adapter(self._adapters, record.driver_key, record.config_version)
            try:
                credentials = adapter.validate_credentials(
                    credentials,
                    config_version=record.config_version,
                )
                secret_ref = await self._secrets.replace_in_transaction(
                    session,
                    secret_context(record, operation=SecretOperation.management),
                    canonical_json(credentials),
                )
            except InternalSecretError as error:
                raise ConnectorError(
                    "credential_conflict", "Connector credentials could not be replaced.", status_code=409
                ) from error
            except ValueError as error:
                raise ConnectorError(
                    "invalid_credentials", "Connector credentials are invalid.", status_code=400
                ) from error
            record.credential_generation = secret_ref.version
            record.version += 1
            record.updated_at = self._clock()
            await session.execute(
                update(ConnectorConnectionRecord)
                .where(
                    ConnectorConnectionRecord.connector_id == record.id,
                    ConnectorConnectionRecord.status == "ready",
                    ConnectorConnectionRecord.deleted_at.is_(None),
                )
                .values(
                    catalog_generation=ConnectorConnectionRecord.catalog_generation + 1,
                    catalog_available_at=record.updated_at,
                )
            )
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation="connector.credentials.replace",
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector",
                resource_id=record.id,
                result_version=record.version,
                now=record.updated_at,
            )
            session.add(
                audit(
                    actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action="connector.credentials.replace",
                    resource_type="connector",
                    resource_id=record.id,
                    now=record.updated_at,
                )
            )
            return record.to_resource()

    async def test(
        self,
        *,
        actor: AuthenticatedActor,
        connector_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectorTestResult:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except ConnectivityManagementValueError as error:
            raise map_management_value_error(error) from error
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            record = await require_connector(session, connector_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.connector_manage)
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=record.workspace_id,
                    operation="connector.test",
                    scope_id=record.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                )
            except ConnectivityManagementValueError as error:
                raise map_management_value_error(error) from error
            if replay is not None:
                return ConnectorTestResult(
                    connector_id=record.id,
                    connector_version=replay.result_version,
                    tested_at=_utc(replay.created_at),
                )
            _require_version(record.version, expected_version)
            if record.status != ConnectorStatus.active.value:
                raise ConnectorError("connector_disabled", "Connector is disabled.", status_code=409)
            frozen_version = record.version
            frozen_credential_generation = record.credential_generation
        try:
            raw = await self._secrets.resolve(
                secret_context(
                    record,
                    operation=SecretOperation.reconciliation,
                    generation=frozen_credential_generation,
                )
            )
            adapter = require_adapter(self._adapters, record.driver_key, record.config_version)
            await adapter.test_connector(
                endpoint=record.endpoint,
                connector_config=record.config_json,
                credentials=decode_credentials(raw),
            )
        except ConnectorAdapterError as error:
            raise external_error(error) from error
        except InternalSecretError as error:
            raise ConnectorError(
                "credential_unavailable", "Connector credentials are unavailable.", status_code=503
            ) from error
        tested_at = self._clock()
        async with transaction(self._sessions) as session:
            current = await require_connector(session, connector_id, lock=True)
            if (
                current.version != frozen_version
                or current.credential_generation != frozen_credential_generation
                or current.status != ConnectorStatus.active.value
            ):
                raise ConnectorError("connector_changed", "Connector changed during its test.", status_code=409)
            record_command(
                session,
                actor=actor,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                operation="connector.test",
                scope_id=current.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector",
                resource_id=current.id,
                result_version=current.version,
                now=tested_at,
            )
            session.add(
                audit(
                    actor,
                    organization_id=current.organization_id,
                    workspace_id=current.workspace_id,
                    action="connector.test",
                    resource_type="connector",
                    resource_id=current.id,
                    now=tested_at,
                )
            )
        return ConnectorTestResult(
            connector_id=connector_id,
            connector_version=frozen_version,
            tested_at=tested_at,
        )

    async def set_status(
        self,
        *,
        actor: AuthenticatedActor,
        connector_id: str,
        status: ConnectorStatus,
        expected_version: int,
        idempotency_key: str,
    ) -> Connector:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except ConnectivityManagementValueError as error:
            raise map_management_value_error(error) from error
        request_fingerprint = canonical_digest({"expected_version": expected_version, "status": status.value})
        async with transaction(self._sessions) as session:
            record = await require_connector(session, connector_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.connector_manage)
            replay = await _replay_connector_command(
                session,
                actor=actor,
                record=record,
                operation="connector.status.set",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
            if replay:
                return record.to_resource()
            _require_version(record.version, expected_version)
            if record.status != status.value:
                record.status = status.value
                record.version += 1
                record.updated_at = self._clock()
            action = "connector.enable" if status is ConnectorStatus.active else "connector.disable"
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation="connector.status.set",
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector",
                resource_id=record.id,
                result_version=record.version,
                now=record.updated_at,
            )
            session.add(
                audit(
                    actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action=action,
                    resource_type="connector",
                    resource_id=record.id,
                    now=record.updated_at,
                )
            )
            return record.to_resource()


def _require_version(current: int, expected: int) -> None:
    if current != expected:
        raise ConnectorError("version_conflict", "Resource version has changed.", status_code=409)


async def _replay_connector_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: ConnectorRecord,
    operation: str,
    key_digest: str,
    request_fingerprint: str,
) -> bool:
    try:
        replay = await replay_command(
            session,
            actor=actor,
            workspace_id=record.workspace_id,
            operation=operation,
            scope_id=record.id,
            idempotency_key_digest=key_digest,
            fingerprint=request_fingerprint,
        )
    except ConnectivityManagementValueError as error:
        raise map_management_value_error(error) from error
    return replay is not None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
