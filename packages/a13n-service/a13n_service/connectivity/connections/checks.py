"""Explicit observations of provider account state or MCP discovery."""

from __future__ import annotations

from contextlib import aclosing

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.connectivity.connectors.connection_access import connection_binding
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError
from a13n_service.connectivity.connectors.management import (
    ProviderSnapshot,
    configure_provider,
    decode_credentials,
    require_connector_provider,
)
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.mcp.service import MCPConnectionService
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .access import ConnectionError, audit, authorize, project, require_connection, require_version
from .domain import ConnectionCheck
from .models import AuthorizationRecord


class ConnectionChecks:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        protector: SecretProtector,
        mcp: MCPConnectionService,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions, self._adapters, self._protector, self._mcp, self._clock = (
            sessions,
            adapters,
            protector,
            mcp,
            clock,
        )

    async def check(self, *, actor: AuthenticatedActor, connection_id: str, expected_version: int) -> Connection:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize(session, actor, connection.workspace_id, mode="manage")
            require_version(connection, expected_version)
            if connection.status == "disabled":
                raise ConnectionError("connection_disabled", "Connection is disabled.", category=ErrorCategory.conflict)
            active_authorization = await session.scalar(
                select(AuthorizationRecord.id)
                .where(
                    AuthorizationRecord.connection_id == connection_id,
                    AuthorizationRecord.generation == connection.setup_generation,
                    AuthorizationRecord.status.not_in(("completed", "failed", "expired", "cancelled")),
                )
                .limit(1)
            )
            if active_authorization is not None:
                raise ConnectionError(
                    "authorization_pending",
                    "Complete authorization before checking the connection.",
                    category=ErrorCategory.conflict,
                )
            generation = connection.authorization_generation
            kind = connection.kind
            provider_snapshot = None
            credentials = None
            binding = None
            provider_generation = None
            if isinstance(connection, ConnectorConnectionRecord):
                binding = connection_binding(connection)
                provider = await require_connector_provider(
                    session,
                    connection.connector_provider_id,
                    scope=ResourceScope(connection.organization_id, connection.workspace_id),
                )
                if provider.status != "active":
                    raise ConnectionError(
                        "provider_disabled", "Connector provider is disabled.", category=ErrorCategory.conflict
                    )
                provider_snapshot = ProviderSnapshot.from_record(provider)
                provider_generation = provider.credential_generation
                credentials = decode_credentials(provider.credential_snapshot().decrypt(self._protector))
        inspection = None
        error_code = None
        try:
            if kind == "mcp":
                await self._mcp.discover_tools(
                    actor=actor, connection_id=connection_id, expected_version=expected_version
                )
            else:
                assert provider_snapshot is not None and credentials is not None and binding is not None
                runtime = configure_provider(self._adapters, provider_snapshot, credentials)
                async with aclosing(runtime), aclosing(runtime.connect(binding)) as account:
                    inspection = await account.inspect()
                    if (
                        inspection.external_ref != binding.external_ref
                        or inspection.connector_key != binding.connector_key
                        or inspection.external_user_correlation != binding.external_user_correlation
                    ):
                        raise ConnectorProviderError("connection_substitution")
        except (ApplicationError, ConnectorProviderError) as error:
            error_code = error.code
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize(session, actor, connection.workspace_id, mode="manage")
            require_version(connection, expected_version)
            if connection.authorization_generation != generation or connection.status == "disabled":
                raise ConnectionError(
                    "connection_changed",
                    "Connection authorization changed during inspection.",
                    category=ErrorCategory.conflict,
                )
            if isinstance(connection, ConnectorConnectionRecord):
                provider = await require_connector_provider(
                    session,
                    connection.connector_provider_id,
                    scope=ResourceScope(connection.organization_id, connection.workspace_id),
                )
                if provider.status != "active" or provider.credential_generation != provider_generation:
                    raise ConnectionError(
                        "provider_changed",
                        "Connector provider changed during inspection.",
                        category=ErrorCategory.conflict,
                    )
            if inspection is not None:
                connection.status = inspection.status.value
                connection.status_reason = inspection.status_reason.value if inspection.status_reason else None
                connection.safe_metadata_json = inspection.safe_metadata
            connection.last_check_json = ConnectionCheck(
                checked_at=self._clock(),
                scope="mcp_discovery" if kind == "mcp" else "provider_account",
                status="unavailable" if error_code else "passed" if connection.status == "ready" else "action_required",
                error_code=error_code,
            ).model_dump(mode="json")
            connection.updated_at = self._clock()
            session.add(audit(actor, connection, action="connection.check", now=connection.updated_at))
            return project(connection)
