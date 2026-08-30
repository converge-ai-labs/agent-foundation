"""Short-transaction Connector resource operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from anyio import CancelScope
from jsonschema import Draft202012Validator
from pydantic import JsonValue
from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction

from .domain import (
    CONNECTION_ID_PREFIX,
    CONNECTOR_ID_PREFIX,
    CONNECTOR_REVISION_ID_PREFIX,
    Connection,
    ConnectionAccount,
    Connector,
    ConnectorCreateResult,
    ConnectorRevision,
    ConnectorRevisionCreateResult,
    CreateConnector,
    CreateConnectorRevision,
    PrincipalRef,
    UpdateConnection,
    UpdateConnector,
    bounded_json_object,
)
from .errors import ConnectorError, ConnectorProviderError, ConnectorReauthorizationRequired
from .models import ConnectionRecord, ConnectorRecord, ConnectorRevisionRecord, TriggerRecord
from .provider import (
    ConnectorConnectionProvider,
    ConnectorProviderConnection,
    ConnectorProviderConnectionResult,
    ConnectorProviderContext,
    ConnectorProviderSecret,
    invoke_provider,
)
from .registry import ConnectorProviderCatalog


class ConnectorService:
    """Connector and immutable revision lifecycle under explicit tenant scope."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        reference_checker: ConnectorReferenceChecker | None = None,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._reference_checker = reference_checker

    async def create(self, request: CreateConnector) -> ConnectorCreateResult:
        config = self._validate_config(
            request.provider_key,
            request.provider_config_version,
            request.config,
        )
        now = datetime.now(UTC)
        connector_record = ConnectorRecord(
            id=new_object_id(CONNECTOR_ID_PREFIX),
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            name=request.name,
            description=request.description,
            enabled=request.enabled,
            version=1,
            created_by_type=request.created_by.principal_type,
            created_by_id=request.created_by.principal_id,
            created_at=now,
            updated_at=now,
        )
        revision_record = ConnectorRevisionRecord(
            id=new_object_id(CONNECTOR_REVISION_ID_PREFIX),
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            connector_id=connector_record.id,
            version=1,
            provider_key=request.provider_key,
            provider_config_version=request.provider_config_version,
            config=config,
            created_by_type=request.created_by.principal_type,
            created_by_id=request.created_by.principal_id,
            created_at=now,
        )
        async with transaction(self._sessions) as session:
            session.add_all((connector_record, revision_record))
        return ConnectorCreateResult(
            connector=_connector(connector_record),
            revision=_revision(revision_record),
        )

    async def get(self, organization_id: str, workspace_id: str, connector_id: str) -> Connector:
        async with short_session(self._sessions) as session:
            record = await _get_connector(session, organization_id, workspace_id, connector_id)
        return _connector(record)

    async def list(
        self,
        organization_id: str,
        workspace_id: str,
        *,
        limit: int = 100,
    ) -> tuple[Connector, ...]:
        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        statement = (
            select(ConnectorRecord)
            .where(
                ConnectorRecord.organization_id == organization_id,
                ConnectorRecord.workspace_id == workspace_id,
            )
            .order_by(ConnectorRecord.updated_at.desc(), ConnectorRecord.id.desc())
            .limit(limit)
        )
        async with short_session(self._sessions) as session:
            records = tuple((await session.scalars(statement)).all())
        return tuple(_connector(record) for record in records)

    async def update(
        self,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        request: UpdateConnector,
    ) -> Connector:
        async with transaction(self._sessions) as session:
            record = await _get_connector(
                session,
                organization_id,
                workspace_id,
                connector_id,
                for_update=True,
            )
            if record.version != request.expected_version:
                raise ConnectorError(
                    "Connector version does not match expected_version.",
                    code="version_conflict",
                    details={"current_version": record.version},
                )
            changed = False
            if "name" in request.model_fields_set and request.name is not None and request.name != record.name:
                record.name = request.name
                changed = True
            if "description" in request.model_fields_set and request.description != record.description:
                record.description = request.description
                changed = True
            if (
                "enabled" in request.model_fields_set
                and request.enabled is not None
                and request.enabled != record.enabled
            ):
                record.enabled = request.enabled
                changed = True
            if changed:
                record.version += 1
                record.updated_at = datetime.now(UTC)
        return _connector(record)

    async def create_revision(
        self,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        request: CreateConnectorRevision,
    ) -> ConnectorRevisionCreateResult:
        config = self._validate_config(
            request.provider_key,
            request.provider_config_version,
            request.config,
        )
        async with transaction(self._sessions) as session:
            await _get_connector(
                session,
                organization_id,
                workspace_id,
                connector_id,
                for_update=True,
            )
            latest = await session.scalar(
                select(ConnectorRevisionRecord)
                .where(
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                    ConnectorRevisionRecord.connector_id == connector_id,
                )
                .order_by(ConnectorRevisionRecord.version.desc())
                .limit(1)
            )
            if latest is None:
                raise ConnectorError("Connector revision history is missing.", code="connector_corrupt")
            if (
                latest.provider_key == request.provider_key
                and latest.provider_config_version == request.provider_config_version
                and latest.config == config
            ):
                return ConnectorRevisionCreateResult(revision=_revision(latest), created=False)
            record = ConnectorRevisionRecord(
                id=new_object_id(CONNECTOR_REVISION_ID_PREFIX),
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                version=latest.version + 1,
                provider_key=request.provider_key,
                provider_config_version=request.provider_config_version,
                config=config,
                created_by_type=request.created_by.principal_type,
                created_by_id=request.created_by.principal_id,
                created_at=datetime.now(UTC),
            )
            session.add(record)
        return ConnectorRevisionCreateResult(revision=_revision(record), created=True)

    async def get_revision(
        self,
        organization_id: str,
        workspace_id: str,
        revision_id: str,
    ) -> ConnectorRevision:
        statement = select(ConnectorRevisionRecord).where(
            ConnectorRevisionRecord.id == revision_id,
            ConnectorRevisionRecord.organization_id == organization_id,
            ConnectorRevisionRecord.workspace_id == workspace_id,
        )
        async with short_session(self._sessions) as session:
            record = await session.scalar(statement)
        if record is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        return _revision(record)

    async def list_revisions(
        self,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ConnectorRevision, ...]:
        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        statement = (
            select(ConnectorRevisionRecord)
            .where(
                ConnectorRevisionRecord.organization_id == organization_id,
                ConnectorRevisionRecord.workspace_id == workspace_id,
                ConnectorRevisionRecord.connector_id == connector_id,
            )
            .order_by(ConnectorRevisionRecord.version.desc())
            .limit(limit)
        )
        async with short_session(self._sessions) as session:
            records = tuple((await session.scalars(statement)).all())
        return tuple(_revision(record) for record in records)

    async def delete(
        self,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
        *,
        expected_version: int,
    ) -> None:
        if self._reference_checker is None:
            raise ConnectorError(
                "Connector reference authority is unavailable.",
                code="dependency_unavailable",
            )
        async with transaction(self._sessions) as session:
            record = await _get_connector(
                session,
                organization_id,
                workspace_id,
                connector_id,
                for_update=True,
            )
            if record.version != expected_version:
                raise ConnectorError(
                    "Connector version does not match expected_version.",
                    code="version_conflict",
                    details={"current_version": record.version},
                )
            connection_count = await session.scalar(
                select(func.count()).select_from(ConnectionRecord).where(ConnectionRecord.connector_id == connector_id)
            )
            trigger_count = await session.scalar(
                select(func.count())
                .select_from(TriggerRecord)
                .join(ConnectorRevisionRecord, TriggerRecord.connector_revision_id == ConnectorRevisionRecord.id)
                .where(ConnectorRevisionRecord.connector_id == connector_id)
            )
            agent_reference = await self._reference_checker.has_agent_revision_reference(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
            )
            if connection_count or trigger_count or agent_reference:
                raise ConnectorError("Connector is still in use.", code="connector_in_use")
            await session.execute(delete(ConnectorRecord).where(ConnectorRecord.id == connector_id))

    def _validate_config(
        self,
        provider_key: str,
        provider_config_version: str,
        config: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        normalized = bounded_json_object(config, field_name="config")
        provider = self._providers.require(provider_key)
        if provider_config_version not in provider.metadata.provider_config_schemas:
            raise ConnectorError(
                "Connector Provider configuration version is unsupported.",
                code="provider_config_incompatible",
                details={"provider_key": provider_key},
            )
        try:
            Draft202012Validator(provider.metadata.provider_config_schemas[provider_config_version]).validate(
                normalized
            )
            provider.validate_config(provider_config_version, normalized)
        except ConnectorProviderError:
            raise
        except Exception:
            raise ConnectorError(
                "Connector Provider rejected the configuration.",
                code="provider_config_incompatible",
                details={"provider_key": provider_key},
            ) from None
        return normalized


class ConnectorReferenceChecker(Protocol):
    """Agent-owned retained reference check required for safe Connector deletion."""

    async def has_agent_revision_reference(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connector_id: str,
    ) -> bool: ...


class ConnectionSecretStore(Protocol):
    """Connection-owned managed Secret operations supplied by the Secret domain."""

    async def replace_connection_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        secrets: Sequence[ConnectorProviderSecret],
        actor: PrincipalRef,
    ) -> None: ...

    async def read_connection_secrets(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
    ) -> tuple[ConnectorProviderSecret, ...]: ...

    async def delete_connection_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        actor: PrincipalRef,
    ) -> None: ...


class ConnectionService:
    """Connection setup result, lifecycle, refresh, and terminal revocation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        secrets: ConnectionSecretStore,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._secrets = secrets

    async def create_from_provider_result(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
        principal_ref: PrincipalRef | None,
        name: str,
        result: ConnectorProviderConnectionResult,
        actor: PrincipalRef,
    ) -> Connection:
        revision, _connector_record = await self._load_revision_and_connector(
            organization_id,
            workspace_id,
            connector_revision_id,
        )
        provider = self._providers.require(revision.provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        try:
            provider.validate_connection(
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=_provider_connection(result),
            )
        except ConnectorProviderError:
            raise
        except Exception:
            raise ConnectorError(
                "Connector Provider rejected the Connection result.",
                code="connection_incompatible",
            ) from None

        now = datetime.now(UTC)
        record = ConnectionRecord(
            id=new_object_id(CONNECTION_ID_PREFIX),
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_id=revision.connector_id,
            principal_type=principal_ref.principal_type if principal_ref else None,
            principal_id=principal_ref.principal_id if principal_ref else None,
            name=name,
            provider_key=revision.provider_key,
            provider_state_version=result.provider_state_version,
            provider_state=bounded_json_object(dict(result.provider_state), field_name="provider_state"),
            account_external_id=result.account.external_id,
            account_display_name=result.account.display_name,
            status="active",
            expires_at=result.expires_at,
            lifecycle_operation_id=None,
            cleanup_pending=False,
            version=1,
            created_by_type=actor.principal_type,
            created_by_id=actor.principal_id,
            created_at=now,
            updated_at=now,
        )
        async with transaction(self._sessions) as session:
            session.add(record)
            await session.flush()
            await self._secrets.replace_connection_secrets(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connection_id=record.id,
                secrets=result.secrets,
                actor=actor,
            )
        return _connection(record)

    async def get(self, organization_id: str, workspace_id: str, connection_id: str) -> Connection:
        async with short_session(self._sessions) as session:
            record = await _get_connection(session, organization_id, workspace_id, connection_id)
        return _connection(record)

    async def list(
        self,
        organization_id: str,
        workspace_id: str,
        *,
        connector_id: str | None = None,
        limit: int = 100,
    ) -> tuple[Connection, ...]:
        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        statement = select(ConnectionRecord).where(
            ConnectionRecord.organization_id == organization_id,
            ConnectionRecord.workspace_id == workspace_id,
        )
        if connector_id is not None:
            statement = statement.where(ConnectionRecord.connector_id == connector_id)
        statement = statement.order_by(ConnectionRecord.updated_at.desc(), ConnectionRecord.id.desc()).limit(limit)
        async with short_session(self._sessions) as session:
            records = tuple((await session.scalars(statement)).all())
        return tuple(_connection(record) for record in records)

    async def rename(
        self,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        request: UpdateConnection,
    ) -> Connection:
        async with transaction(self._sessions) as session:
            record = await _get_connection(
                session,
                organization_id,
                workspace_id,
                connection_id,
                for_update=True,
            )
            _require_version(record.version, request.expected_version, "Connection")
            if request.name is not None and record.name != request.name:
                record.name = request.name
                _advance_connection(record)
        return _connection(record)

    async def disable(
        self,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        *,
        expected_version: int,
    ) -> Connection:
        return await self._transition(
            organization_id,
            workspace_id,
            connection_id,
            expected_version=expected_version,
            allowed={"active"},
            target="disabled",
        )

    async def enable(
        self,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        *,
        expected_version: int,
    ) -> Connection:
        return await self._transition(
            organization_id,
            workspace_id,
            connection_id,
            expected_version=expected_version,
            allowed={"disabled"},
            target="active",
        )

    async def refresh(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        connector_revision_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        actor: PrincipalRef,
    ) -> Connection:
        record, revision = await self._load_connection_and_revision(
            organization_id,
            workspace_id,
            connection_id,
            connector_revision_id,
        )
        _require_version(record.version, expected_version, "Connection")
        if record.status == "revoked":
            raise ConnectorError("A revoked Connection cannot be refreshed.", code="connection_revoked")
        if record.status == "reauthorization_required":
            raise ConnectorError(
                "Connection requires reauthorization.",
                code="connection_reauthorization_required",
            )
        async with transaction(self._sessions) as session:
            current = await _get_connection(
                session,
                organization_id,
                workspace_id,
                connection_id,
                for_update=True,
            )
            _require_version(current.version, expected_version, "Connection")
            if current.lifecycle_operation_id is None:
                current.lifecycle_operation_id = context.operation_id
            operation_id = current.lifecycle_operation_id
        effective_context = ConnectorProviderContext(operation_id=operation_id, deadline=context.deadline)
        secrets = await self._secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
        provider = self._providers.require(record.provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        try:
            result = await invoke_provider(
                effective_context,
                provider.refresh_connection(
                    effective_context,
                    provider_config_version=revision.provider_config_version,
                    config=revision.config,
                    connection=_record_provider_connection(record, secrets),
                ),
            )
        except ConnectorReauthorizationRequired:
            async with transaction(self._sessions) as session:
                current = await _get_connection(
                    session,
                    organization_id,
                    workspace_id,
                    connection_id,
                    for_update=True,
                )
                _require_version(current.version, expected_version, "Connection")
                if current.status != "reauthorization_required":
                    current.status = "reauthorization_required"
                    _advance_connection(current)
                current.lifecycle_operation_id = None
            raise
        provider.validate_connection(
            provider_config_version=revision.provider_config_version,
            config=revision.config,
            connection=_provider_connection(result),
        )
        provider_state = bounded_json_object(dict(result.provider_state), field_name="provider_state")
        async with transaction(self._sessions) as session:
            current = await _get_connection(
                session,
                organization_id,
                workspace_id,
                connection_id,
                for_update=True,
            )
            _require_version(current.version, expected_version, "Connection")
            if current.lifecycle_operation_id != operation_id:
                raise ConnectorError("Connection refresh was superseded.", code="version_conflict")
            if current.status == "revoked":
                raise ConnectorError("A revoked Connection cannot be refreshed.", code="connection_revoked")
            changed = (
                current.provider_state_version != result.provider_state_version
                or current.provider_state != provider_state
                or current.account_external_id != result.account.external_id
                or current.account_display_name != result.account.display_name
                or current.expires_at != result.expires_at
            )
            await self._secrets.replace_connection_secrets(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connection_id=connection_id,
                secrets=result.secrets,
                actor=actor,
            )
            current.provider_state_version = result.provider_state_version
            current.provider_state = provider_state
            current.account_external_id = result.account.external_id
            current.account_display_name = result.account.display_name
            current.expires_at = result.expires_at
            current.lifecycle_operation_id = None
            if changed:
                _advance_connection(current)
        return _connection(current)

    async def revoke(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        connector_revision_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        actor: PrincipalRef,
    ) -> Connection:
        record, revision = await self._load_connection_and_revision(
            organization_id,
            workspace_id,
            connection_id,
            connector_revision_id,
        )
        _require_version(record.version, expected_version, "Connection")
        if record.status == "revoked":
            return _connection(record)
        secrets = await self._secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
        provider = self._providers.require(record.provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        provider_error: BaseException | None = None
        try:
            await invoke_provider(
                context,
                provider.revoke_connection(
                    context,
                    provider_config_version=revision.provider_config_version,
                    config=revision.config,
                    connection=_record_provider_connection(record, secrets),
                ),
            )
        except BaseException as error:
            provider_error = error
        cleanup_pending = provider_error is not None
        current = record
        with CancelScope(shield=True):
            async with transaction(self._sessions) as session:
                current = await _get_connection(
                    session,
                    organization_id,
                    workspace_id,
                    connection_id,
                    for_update=True,
                )
                _require_version(current.version, expected_version, "Connection")
                if current.status != "revoked":
                    current.status = "revoked"
                    _advance_connection(current)
                current.cleanup_pending = cleanup_pending
                current.lifecycle_operation_id = context.operation_id if cleanup_pending else None
                if not cleanup_pending:
                    await self._secrets.delete_connection_secrets(
                        session,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        connection_id=connection_id,
                        actor=actor,
                    )
        if provider_error is not None and not isinstance(provider_error, Exception):
            raise provider_error
        return _connection(current)

    async def reconcile_revoke(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        connector_revision_id: str,
        context: ConnectorProviderContext,
    ) -> Connection:
        record, revision = await self._load_connection_and_revision(
            organization_id,
            workspace_id,
            connection_id,
            connector_revision_id,
        )
        if record.status != "revoked":
            raise ConnectorError("Connection is not revoked.", code="connection_incompatible")
        if not record.cleanup_pending:
            return _connection(record)
        provider = self._providers.require(record.provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        secrets = await self._secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
        effective_context = ConnectorProviderContext(
            operation_id=record.lifecycle_operation_id or context.operation_id,
            deadline=context.deadline,
        )
        await invoke_provider(
            effective_context,
            provider.revoke_connection(
                effective_context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=_record_provider_connection(record, secrets),
            ),
        )
        async with transaction(self._sessions) as session:
            current = await _get_connection(
                session,
                organization_id,
                workspace_id,
                connection_id,
                for_update=True,
            )
            if current.status == "revoked" and current.cleanup_pending:
                await self._secrets.delete_connection_secrets(
                    session,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    actor=PrincipalRef.model_validate(
                        {
                            "principal_type": current.created_by_type,
                            "principal_id": current.created_by_id,
                        }
                    ),
                )
                current.cleanup_pending = False
                current.lifecycle_operation_id = None
                _advance_connection(current)
        return _connection(current)

    async def _transition(
        self,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        *,
        expected_version: int,
        allowed: set[str],
        target: str,
    ) -> Connection:
        async with transaction(self._sessions) as session:
            record = await _get_connection(
                session,
                organization_id,
                workspace_id,
                connection_id,
                for_update=True,
            )
            _require_version(record.version, expected_version, "Connection")
            if record.status not in allowed:
                raise ConnectorError(
                    "Connection lifecycle transition is invalid.",
                    code=f"connection_{record.status}",
                )
            record.status = target
            _advance_connection(record)
        return _connection(record)

    async def _load_revision_and_connector(
        self,
        organization_id: str,
        workspace_id: str,
        connector_revision_id: str,
    ) -> tuple[ConnectorRevisionRecord, ConnectorRecord]:
        async with short_session(self._sessions) as session:
            revision = await session.scalar(
                select(ConnectorRevisionRecord).where(
                    ConnectorRevisionRecord.id == connector_revision_id,
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                )
            )
            if revision is None:
                raise ConnectorError("Connector revision was not found.", code="not_found")
            connector = await _get_connector(session, organization_id, workspace_id, revision.connector_id)
        if not connector.enabled:
            raise ConnectorError("Connector is disabled.", code="connector_disabled")
        return revision, connector

    async def _load_connection_and_revision(
        self,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        connector_revision_id: str,
    ) -> tuple[ConnectionRecord, ConnectorRevisionRecord]:
        async with short_session(self._sessions) as session:
            record = await _get_connection(session, organization_id, workspace_id, connection_id)
            revision = await session.scalar(
                select(ConnectorRevisionRecord).where(
                    ConnectorRevisionRecord.id == connector_revision_id,
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                    ConnectorRevisionRecord.connector_id == record.connector_id,
                    ConnectorRevisionRecord.provider_key == record.provider_key,
                )
            )
        if revision is None:
            raise ConnectorError(
                "Connection is incompatible with the Connector revision.", code="connection_incompatible"
            )
        return record, revision


async def resolve_connection(
    sessions: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    workspace_id: str,
    connector_id: str,
    provider_key: str,
    principal: PrincipalRef,
    pinned_connection_id: str | None,
) -> Connection | None:
    """Resolve the exact personal-then-shared Connection required at acceptance."""

    base = (
        select(ConnectionRecord)
        .where(
            ConnectionRecord.organization_id == organization_id,
            ConnectionRecord.workspace_id == workspace_id,
            ConnectionRecord.connector_id == connector_id,
            ConnectionRecord.provider_key == provider_key,
            ConnectionRecord.status == "active",
        )
        .order_by(ConnectionRecord.id)
    )
    async with short_session(sessions) as session:
        if pinned_connection_id is not None:
            record = await session.scalar(base.where(ConnectionRecord.id == pinned_connection_id))
            if record is None or not _principal_can_use(record, principal):
                raise ConnectorError("Pinned Connection is unavailable.", code="connection_required")
            return _connection(record)

        personal = tuple(
            (
                await session.scalars(
                    base.where(
                        ConnectionRecord.principal_type == principal.principal_type,
                        ConnectionRecord.principal_id == principal.principal_id,
                    ).limit(2)
                )
            ).all()
        )
        if len(personal) > 1:
            raise ConnectorError("Several personal Connections are eligible.", code="connection_ambiguous")
        if personal:
            return _connection(personal[0])
        shared = tuple(
            (
                await session.scalars(
                    base.where(
                        ConnectionRecord.principal_type.is_(None),
                        ConnectionRecord.principal_id.is_(None),
                    ).limit(2)
                )
            ).all()
        )
    if len(shared) > 1:
        raise ConnectorError("Several shared Connections are eligible.", code="connection_ambiguous")
    if shared:
        return _connection(shared[0])
    raise ConnectorError("No eligible Connection is available.", code="connection_required")


async def _get_connector(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    connector_id: str,
    *,
    for_update: bool = False,
) -> ConnectorRecord:
    statement: Select[tuple[ConnectorRecord]] = select(ConnectorRecord).where(
        ConnectorRecord.id == connector_id,
        ConnectorRecord.organization_id == organization_id,
        ConnectorRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise ConnectorError("Connector was not found.", code="not_found")
    return record


async def _get_connection(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    connection_id: str,
    *,
    for_update: bool = False,
) -> ConnectionRecord:
    statement: Select[tuple[ConnectionRecord]] = select(ConnectionRecord).where(
        ConnectionRecord.id == connection_id,
        ConnectionRecord.organization_id == organization_id,
        ConnectionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise ConnectorError("Connection was not found.", code="not_found")
    return record


def _principal(record_type: str, record_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=record_type, principal_id=record_id)  # type: ignore[arg-type]


def _connector(record: ConnectorRecord) -> Connector:
    return Connector(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        enabled=record.enabled,
        version=record.version,
        created_by=_principal(record.created_by_type, record.created_by_id),
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
    )


def _revision(record: ConnectorRevisionRecord) -> ConnectorRevision:
    return ConnectorRevision(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        connector_id=record.connector_id,
        version=record.version,
        provider_key=record.provider_key,
        provider_config_version=record.provider_config_version,
        config=record.config,
        created_by=_principal(record.created_by_type, record.created_by_id),
        created_at=_utc(record.created_at),
    )


def _connection(record: ConnectionRecord) -> Connection:
    principal_ref = None
    if record.principal_type is not None and record.principal_id is not None:
        principal_ref = _principal(record.principal_type, record.principal_id)
    return Connection(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        connector_id=record.connector_id,
        principal_ref=principal_ref,
        name=record.name,
        provider_key=record.provider_key,
        account=ConnectionAccount(
            external_id=record.account_external_id,
            display_name=record.account_display_name,
        ),
        status=record.status,  # type: ignore[arg-type]
        expires_at=_utc(record.expires_at) if record.expires_at is not None else None,
        version=record.version,
        created_by=_principal(record.created_by_type, record.created_by_id),
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
    )


def _principal_can_use(record: ConnectionRecord, principal: PrincipalRef) -> bool:
    if record.principal_type is None:
        return True
    return record.principal_type == principal.principal_type and record.principal_id == principal.principal_id


def _provider_connection(result: ConnectorProviderConnectionResult) -> ConnectorProviderConnection:
    return ConnectorProviderConnection(
        provider_state_version=result.provider_state_version,
        provider_state=result.provider_state,
        secrets=result.secrets,
    )


def _record_provider_connection(
    record: ConnectionRecord,
    secrets: tuple[ConnectorProviderSecret, ...],
) -> ConnectorProviderConnection:
    return ConnectorProviderConnection(
        provider_state_version=record.provider_state_version,
        provider_state=record.provider_state,
        secrets=secrets,
    )


def _require_version(current: int, expected: int, resource: str) -> None:
    if current != expected:
        raise ConnectorError(
            f"{resource} version does not match expected_version.",
            code="version_conflict",
            details={"current_version": current},
        )


def _advance_connection(record: ConnectionRecord) -> None:
    record.version += 1
    record.updated_at = datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
