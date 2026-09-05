"""Transactional ConnectorProvider resource management."""

from __future__ import annotations

from asyncio import timeout
from contextlib import aclosing
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.management import (
    CommandReceipt,
    canonical_digest,
    canonical_json,
    clear_credentials,
    fingerprint,
    idempotency_key_digest,
    record_command,
    replay_command,
)
from a13n_service.durable_operations.idempotency import IdempotencyConflict, InvalidIdempotencyKey
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .connection_access import external_error
from .discovery import validate_connectors
from .domain import (
    Connector,
    ConnectorCollection,
    ConnectorProvider,
    ConnectorProviderCollection,
    ConnectorProviderStatus,
    ConnectorProviderTestResult,
    CreateConnectorProviderRequest,
    ReplaceConnectorProviderCredentialsRequest,
    UpdateConnectorProviderRequest,
)
from .errors import ConnectorError
from .management import (
    audit,
    authorize,
    authorize_provider,
    configure_provider,
    connector_actor_scope,
    decode_credentials,
    map_management_value_error,
    require_connector_provider,
    require_implementation,
)
from .models import ConnectorProviderRecord
from .registry import ConnectorProviderDefinitionCollection


class ConnectorProviderService:
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

    async def type_definitions(self, *, actor: AuthenticatedActor) -> ConnectorProviderDefinitionCollection:
        async with transaction(self._sessions) as session:
            await authorize(session, actor, actor.boundary_workspace_id, WorkspaceAction.connector_provider_read)
        return ConnectorProviderDefinitionCollection(items=self._adapters.definitions())

    async def discover_connectors(
        self, *, actor: AuthenticatedActor, connector_provider_id: str
    ) -> ConnectorCollection:
        async with transaction(self._sessions) as session:
            record = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor)
            )
            await authorize_provider(session, actor, record, manage=False)
            if record.status != ConnectorProviderStatus.active.value:
                raise ConnectorError(
                    "connector_provider_disabled", "Connector Provider is disabled.", category=ErrorCategory.conflict
                )
            generation = record.credential_generation
        try:
            raw = record.credential_snapshot().decrypt(self._protector)
            async with (
                timeout(30),
                aclosing(configure_provider(self._adapters, record, decode_credentials(raw))) as runtime,
            ):
                discovered = await runtime.discover_connectors()
            validate_connectors(discovered)
        except TimeoutError as error:
            raise ConnectorError(
                "connector_provider_unavailable",
                "Connector Provider discovery timed out.",
                category=ErrorCategory.unavailable,
            ) from error
        except ConnectorProviderError as error:
            raise external_error(error) from error
        except SecretProtectionError as error:
            raise ConnectorError(
                "credential_unavailable",
                "Connector Provider credentials are unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        except ValueError as error:
            raise ConnectorError(
                "invalid_provider_response",
                "Connector Provider returned an invalid directory.",
                category=ErrorCategory.dependency_failure,
            ) from error
        async with transaction(self._sessions) as session:
            current = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor)
            )
            await authorize_provider(session, actor, current, manage=False)
            if current.status != ConnectorProviderStatus.active.value or current.credential_generation != generation:
                raise ConnectorError(
                    "connector_provider_changed",
                    "Connector Provider changed during discovery.",
                    category=ErrorCategory.conflict,
                )
        return ConnectorCollection(
            items=tuple(Connector(connector_provider_id=record.id, **item.model_dump()) for item in discovered)
        )

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        idempotency_key: str,
        request: CreateConnectorProviderRequest,
    ) -> ConnectorProvider:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except InvalidIdempotencyKey as error:
            raise map_management_value_error(error) from error
        connector_provider_id = new_object_id("cnr")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, WorkspaceAction.connector_provider_manage)
                adapter = require_implementation(self._adapters, request.type)
                try:
                    configuration = adapter.validate_configuration(request.configuration)
                    credentials = adapter.validate_credentials(
                        clear_credentials(request.credentials),
                    )
                except ValueError as error:
                    raise ConnectorError(
                        "invalid_connector_provider_configuration",
                        "ConnectorProvider configuration is invalid.",
                        category=ErrorCategory.invalid_request,
                    ) from error
                request_fingerprint = fingerprint(request, credentials=credentials)
                try:
                    replay = await replay_command(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        operation="connector_provider.create",
                        scope_id=workspace.id,
                        idempotency_key_digest=key_digest,
                        fingerprint=request_fingerprint,
                        now=self._clock(),
                    )
                except IdempotencyConflict as error:
                    raise map_management_value_error(error) from error
                if replay is not None:
                    return replay.restore(ConnectorProvider)
                record = ConnectorProviderRecord(
                    id=connector_provider_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    type=request.type,
                    configuration_json=configuration,
                    status=ConnectorProviderStatus.active.value,
                    version=1,
                    credential_generation=0,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                record.replace_credential(canonical_json(credentials), self._protector)

                session.add(record)
                record_command(
                    session,
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    operation="connector_provider.create",
                    scope_id=workspace.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_provider",
                    resource_id=connector_provider_id,
                    result_version=1,
                    now=now,
                    resource=record.to_resource(),
                )
                session.add(
                    audit(
                        actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        action="connector_provider.create",
                        resource_type="connector_provider",
                        resource_id=connector_provider_id,
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise ConnectorError(
                "connector_conflict",
                "ConnectorProvider identity or name already exists.",
                category=ErrorCategory.conflict,
            ) from error
        except SecretProtectionError as error:
            raise ConnectorError(
                "credential_conflict",
                "ConnectorProvider credentials could not be stored.",
                category=ErrorCategory.conflict,
            ) from error

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> ConnectorProviderCollection:
        if not 1 <= limit <= 100:
            raise ConnectorError(
                "invalid_request", "Collection limit is invalid.", category=ErrorCategory.invalid_request
            )
        scope = {
            "resource_type": "connector_provider",
            "workspace_id": workspace_id,
            "organization_boundary": actor.boundary_organization_id,
            "actor": actor.principal.model_dump(mode="json"),
        }
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="cnr") if cursor else None
        except CursorError as error:
            raise ConnectorError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize(session, actor, workspace_id, WorkspaceAction.connector_provider_read)
            query = select(ConnectorProviderRecord).where(
                ConnectorProviderRecord.organization_id == workspace.organization_id,
                visible_workspace(ConnectorProviderRecord.workspace_id, workspace_id),
            )
            if position is not None:
                query = query.where(
                    or_(
                        ConnectorProviderRecord.updated_at < position[0],
                        and_(
                            ConnectorProviderRecord.updated_at == position[0], ConnectorProviderRecord.id < position[1]
                        ),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(
                            ConnectorProviderRecord.updated_at.desc(), ConnectorProviderRecord.id.desc()
                        ).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return ConnectorProviderCollection(
                items=tuple(item.to_resource() for item in page), next_cursor=next_cursor
            )

    async def get(self, *, actor: AuthenticatedActor, connector_provider_id: str) -> ConnectorProvider:
        async with transaction(self._sessions) as session:
            record = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor)
            )
            await authorize_provider(session, actor, record, manage=False)
            return record.to_resource()

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        connector_provider_id: str,
        request: UpdateConnectorProviderRequest,
    ) -> ConnectorProvider:
        try:
            async with transaction(self._sessions) as session:
                record = await require_connector_provider(
                    session, connector_provider_id, scope=await connector_actor_scope(session, actor), lock=True
                )
                await authorize_provider(session, actor, record, manage=True)
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
                        action="connector_provider.update",
                        resource_type="connector_provider",
                        resource_id=record.id,
                        now=record.updated_at,
                    )
                )
                return record.to_resource()
        except IntegrityError as error:
            raise ConnectorError(
                "connector_conflict", "ConnectorProvider name already exists.", category=ErrorCategory.conflict
            ) from error

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connector_provider_id: str,
        idempotency_key: str,
        request: ReplaceConnectorProviderCredentialsRequest,
    ) -> ConnectorProvider:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except InvalidIdempotencyKey as error:
            raise map_management_value_error(error) from error
        credentials = clear_credentials(request.credentials)
        request_fingerprint = fingerprint(request, credentials=credentials)
        async with transaction(self._sessions) as session:
            record = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor), lock=True
            )
            await authorize_provider(session, actor, record, manage=True)
            replay = await _replay_connector_command(
                session,
                actor=actor,
                record=record,
                operation="connector_provider.credentials.replace",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
                now=self._clock(),
            )
            if replay:
                return replay.restore(ConnectorProvider)
            _require_version(record.version, request.expected_version)
            adapter = require_implementation(self._adapters, record.type)
            try:
                credentials = adapter.validate_credentials(
                    credentials,
                )
                record.replace_credential(canonical_json(credentials), self._protector)
            except SecretProtectionError as error:
                raise ConnectorError(
                    "credential_conflict",
                    "ConnectorProvider credentials could not be replaced.",
                    category=ErrorCategory.conflict,
                ) from error
            except ValueError as error:
                raise ConnectorError(
                    "invalid_credentials",
                    "ConnectorProvider credentials are invalid.",
                    category=ErrorCategory.invalid_request,
                ) from error

            record.version += 1
            record.updated_at = self._clock()
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation="connector_provider.credentials.replace",
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector_provider",
                resource_id=record.id,
                result_version=record.version,
                now=self._clock(),
                resource=record.to_resource(),
            )
            session.add(
                audit(
                    actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action="connector_provider.credentials.replace",
                    resource_type="connector_provider",
                    resource_id=record.id,
                    now=record.updated_at,
                )
            )
            return record.to_resource()

    async def test(
        self,
        *,
        actor: AuthenticatedActor,
        connector_provider_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectorProviderTestResult:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except InvalidIdempotencyKey as error:
            raise map_management_value_error(error) from error
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            record = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor)
            )
            await authorize_provider(session, actor, record, manage=True)
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=record.workspace_id,
                    operation="connector_provider.test",
                    scope_id=record.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    now=self._clock(),
                )
            except IdempotencyConflict as error:
                raise map_management_value_error(error) from error
            if replay is not None:
                return ConnectorProviderTestResult(
                    connector_provider_id=record.id,
                    connector_provider_version=replay.result_version,
                    tested_at=assume_utc(replay.created_at),
                )
            _require_version(record.version, expected_version)
            if record.status != ConnectorProviderStatus.active.value:
                raise ConnectorError(
                    "connector_disabled", "ConnectorProvider is disabled.", category=ErrorCategory.conflict
                )
            frozen_version = record.version
            frozen_credential_generation = record.credential_generation
        try:
            raw = record.credential_snapshot().decrypt(self._protector)
            adapter = require_implementation(self._adapters, record.type)
            async with aclosing(adapter.configure(record.configuration_json, decode_credentials(raw))) as runtime:
                await runtime.test()
        except ConnectorProviderError as error:
            raise external_error(error) from error
        except SecretProtectionError as error:
            raise ConnectorError(
                "credential_unavailable",
                "ConnectorProvider credentials are unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        tested_at = self._clock()
        async with transaction(self._sessions) as session:
            current = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor), lock=True
            )
            await authorize_provider(session, actor, current, manage=True)
            replay = await _replay_connector_command(
                session,
                actor=actor,
                record=current,
                operation="connector_provider.test",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConnectorProviderTestResult)
            if (
                current.version != frozen_version
                or current.credential_generation != frozen_credential_generation
                or current.status != ConnectorProviderStatus.active.value
            ):
                raise ConnectorError(
                    "connector_changed", "ConnectorProvider changed during its test.", category=ErrorCategory.conflict
                )
            record_command(
                session,
                actor=actor,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                operation="connector_provider.test",
                scope_id=current.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector_provider",
                resource_id=current.id,
                result_version=current.version,
                now=tested_at,
                resource=ConnectorProviderTestResult(
                    connector_provider_id=current.id, connector_provider_version=current.version, tested_at=tested_at
                ),
            )
            session.add(
                audit(
                    actor,
                    organization_id=current.organization_id,
                    workspace_id=current.workspace_id,
                    action="connector_provider.test",
                    resource_type="connector_provider",
                    resource_id=current.id,
                    now=tested_at,
                )
            )
        return ConnectorProviderTestResult(
            connector_provider_id=connector_provider_id,
            connector_provider_version=frozen_version,
            tested_at=tested_at,
        )

    async def set_status(
        self,
        *,
        actor: AuthenticatedActor,
        connector_provider_id: str,
        status: ConnectorProviderStatus,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectorProvider:
        try:
            key_digest = idempotency_key_digest(idempotency_key)
        except InvalidIdempotencyKey as error:
            raise map_management_value_error(error) from error
        request_fingerprint = canonical_digest({"expected_version": expected_version, "status": status.value})
        async with transaction(self._sessions) as session:
            record = await require_connector_provider(
                session, connector_provider_id, scope=await connector_actor_scope(session, actor), lock=True
            )
            await authorize_provider(session, actor, record, manage=True)
            replay = await _replay_connector_command(
                session,
                actor=actor,
                record=record,
                operation="connector_provider.status.set",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
                now=self._clock(),
            )
            if replay:
                return replay.restore(ConnectorProvider)
            _require_version(record.version, expected_version)
            if record.status != status.value:
                record.status = status.value
                record.version += 1
                record.updated_at = self._clock()
            action = (
                "connector_provider.enable"
                if status is ConnectorProviderStatus.active
                else "connector_provider.disable"
            )
            record_command(
                session,
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                operation="connector_provider.status.set",
                scope_id=record.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="connector_provider",
                resource_id=record.id,
                result_version=record.version,
                now=self._clock(),
                resource=record.to_resource(),
            )
            session.add(
                audit(
                    actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action=action,
                    resource_type="connector_provider",
                    resource_id=record.id,
                    now=record.updated_at,
                )
            )
            return record.to_resource()


def _require_version(current: int, expected: int) -> None:
    if current != expected:
        raise ConnectorError("version_conflict", "Resource version has changed.", category=ErrorCategory.conflict)


async def _replay_connector_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: ConnectorProviderRecord,
    operation: str,
    key_digest: str,
    request_fingerprint: str,
    now: datetime,
) -> CommandReceipt | None:
    try:
        replay = await replay_command(
            session,
            actor=actor,
            workspace_id=record.workspace_id,
            operation=operation,
            scope_id=record.id,
            idempotency_key_digest=key_digest,
            fingerprint=request_fingerprint,
            now=now,
        )
    except IdempotencyConflict as error:
        raise map_management_value_error(error) from error
    return replay
