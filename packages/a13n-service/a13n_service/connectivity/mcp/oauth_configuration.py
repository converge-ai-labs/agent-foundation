"""Connection-owned OAuth application configuration and discovery."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock

from .domain import (
    ConfigureMCPOAuthClientRequest,
    MCPConnection,
    MCPOAuthClientConfiguration,
    MCPOAuthClientInput,
    MCPOAuthDiscovery,
)
from .errors import MCPConnectionError
from .management import audit, authorize_connection, invalidate_refresh_claim, require_connection, require_version
from .models import MCPConnectionOAuthClientRecord, MCPConnectionRecord, MCPOAuthSessionRecord
from .oauth_client import MCPOAuthClient, MCPOAuthError, issuer_key, oauth_redirect_uri


def require_oauth(connection: MCPConnectionRecord) -> None:
    if connection.auth_mode != "oauth":
        raise MCPConnectionError(
            "invalid_auth_mode", "MCPConnection does not use OAuth.", category=ErrorCategory.conflict
        )


def configured_client(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> MCPOAuthClientInput:
    values = dict(record.configuration_json)
    values["client_secret"] = record.credential_snapshot().decrypt(protector) if record.ciphertext is not None else None
    return MCPOAuthClientInput.model_validate(values)


class OAuthConfiguration:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        oauth: MCPOAuthClient,
        protector: SecretProtector,
        *,
        public_origin: str,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._oauth = oauth
        self._protector = protector
        self._public_origin = public_origin
        self._clock = clock

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPOAuthClientConfiguration | None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            return MCPOAuthClientConfiguration.model_validate(record.configuration_json) if record is not None else None

    async def discover(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPOAuthDiscovery:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            endpoint = connection.endpoint_url
        try:
            discovered = await self._oauth.discover(endpoint)
        except MCPOAuthError as error:
            raise MCPConnectionError(
                "mcp_oauth_discovery_failed", "OAuth discovery could not be completed.", category=ErrorCategory.conflict
            ) from error
        return MCPOAuthDiscovery(
            issuer_url=discovered.issuer_url,
            redirect_uri=oauth_redirect_uri(self._public_origin, issuer_key(discovered.issuer_url)),
            token_endpoint_auth_methods_supported=discovered.token_auth_methods,
            authorization_response_iss_parameter_supported=(
                discovered.metadata.get("authorization_response_iss_parameter_supported") is True
            ),
        )

    async def configure(
        self, *, actor: AuthenticatedActor, connection_id: str, request: ConfigureMCPOAuthClientRequest
    ) -> MCPConnection:
        # Discovery is outside the transaction; the version fence prevents a
        # concurrent configuration change from being overwritten afterwards.
        client = request.client
        if client is not None:
            discovered = await self.discover(actor=actor, connection_id=connection_id)
            if (
                client.issuer_url != discovered.issuer_url
                or client.token_endpoint_auth_method not in discovered.token_endpoint_auth_methods_supported
            ):
                raise MCPConnectionError(
                    "invalid_oauth_client",
                    "OAuth client must match the discovered issuer and authentication methods.",
                    category=ErrorCategory.invalid_request,
                )
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            require_version(connection.version, request.expected_version)
            record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            if client is None:
                if record is not None:
                    await session.delete(record)
            else:
                if record is None:
                    record = MCPConnectionOAuthClientRecord(
                        id=connection.id,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        credential_generation=0,
                    )
                    session.add(record)
                record.configuration_json = client.model_dump(mode="json", exclude={"client_secret"})
                record.replace_credential(
                    client.client_secret.get_secret_value() if client.client_secret is not None else None,
                    self._protector,
                )
            connection.replace_credential(None, self._protector)
            connection.version += 1
            connection.updated_at = now
            if connection.status != "disabled":
                connection.status = "pending"
                connection.status_reason = None
            invalidate_refresh_claim(connection, now=now)
            setups = await session.scalars(
                select(MCPOAuthSessionRecord).where(
                    MCPOAuthSessionRecord.mcp_connection_id == connection_id,
                    MCPOAuthSessionRecord.status.in_(("pending", "received", "exchanging")),
                )
            )
            for setup in setups:
                setup.status = "expired"
                setup.clear_credential()
                setup.claim_owner = None
                setup.claim_expires_at = None
                setup.updated_at = now
            session.add(audit(actor, connection, action="mcp_connection.configure_oauth_client", now=now))
            return connection.to_resource()
