"""Connection-owned OAuth application configuration and discovery."""

from __future__ import annotations

import json

import httpx2
from anyio import fail_after
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
    OAuthClientSource,
    OAuthGrantType,
)
from .errors import MCPConnectionError
from .management import audit, authorize_connection, invalidate_refresh_claim, require_connection, require_version
from .models import MCPConnectionOAuthClientRecord, MCPConnectionRecord, MCPOAuthSessionRecord
from .oauth_client import (
    MCPOAuthClient,
    MCPOAuthError,
    OAuthClientContext,
    OAuthDiscovery,
    issuer_key,
    oauth_redirect_uri,
)


def require_oauth(connection: MCPConnectionRecord) -> None:
    if connection.auth_mode != "oauth":
        raise MCPConnectionError(
            "invalid_auth_mode", "MCPConnection does not use OAuth.", category=ErrorCategory.conflict
        )


def configured_client(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> MCPOAuthClientInput:
    values = dict(record.configuration_json)
    values.pop("source", None)
    protected = _protected_client(record, protector)
    values["client_secret"] = protected.get("client_secret")
    return MCPOAuthClientInput.model_validate(values)


def client_configuration(record: MCPConnectionOAuthClientRecord) -> MCPOAuthClientConfiguration:
    return MCPOAuthClientConfiguration.model_validate(record.configuration_json)


def store_client(
    record: MCPConnectionOAuthClientRecord,
    client: MCPOAuthClientInput,
    *,
    source: OAuthClientSource,
    protector: SecretProtector,
    resource_url: str,
    token_endpoint: str,
    scope: str | None,
    registration_access_token: str | None = None,
    registration_client_uri: str | None = None,
) -> None:
    record.configuration_json = {
        **client.model_dump(mode="json", exclude={"client_secret"}),
        "source": source,
    }
    protected = {
        "client_secret": client.client_secret.get_secret_value() if client.client_secret is not None else None,
        "registration_access_token": registration_access_token,
        "registration_client_uri": registration_client_uri,
        "resource_url": resource_url,
        "token_endpoint": token_endpoint,
        "scope": scope,
    }
    record.replace_credential(json.dumps(protected, separators=(",", ":")), protector)


def client_cleanup_bundle(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> dict[str, object]:
    return _protected_client(record, protector)


def client_refresh_context(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> OAuthClientContext:
    client = configured_client(record, protector)
    protected = _protected_client(record, protector)
    resource_url = protected.get("resource_url")
    token_endpoint = protected.get("token_endpoint")
    scope = protected.get("scope")
    if (
        not isinstance(resource_url, str)
        or not resource_url
        or not isinstance(token_endpoint, str)
        or not token_endpoint
    ):
        raise ValueError("invalid protected OAuth client")
    if scope is not None and (not isinstance(scope, str) or not scope):
        raise ValueError("invalid protected OAuth client")
    return OAuthClientContext(
        resource_url=resource_url,
        issuer_url=client.issuer_url,
        token_endpoint=token_endpoint,
        client_id=client.client_id,
        client_secret=client.client_secret.get_secret_value() if client.client_secret is not None else None,
        token_endpoint_auth_method=client.token_endpoint_auth_method,
        scope=scope,
        grant_type=client.grant_type,
    )


def _supports_client(client: MCPOAuthClientInput, discovery: MCPOAuthDiscovery) -> bool:
    return (
        client.issuer_url == discovery.issuer_url
        and client.token_endpoint_auth_method in discovery.token_endpoint_auth_methods_supported
        and client.grant_type in discovery.grant_types_supported
    )


def _protected_client(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> dict[str, object]:
    if record.ciphertext is None:
        return {}
    value = json.loads(record.credential_snapshot().decrypt(protector))
    if not isinstance(value, dict):
        raise ValueError("invalid protected OAuth client")
    return value


async def _replace_client(
    session: AsyncSession,
    connection: MCPConnectionRecord,
    client: MCPOAuthClientInput | None,
    discovery: OAuthDiscovery | None,
    protector: SecretProtector,
) -> dict[str, object] | None:
    record = await session.get(MCPConnectionOAuthClientRecord, connection.id)
    cleanup = (
        client_cleanup_bundle(record, protector)
        if record is not None and record.configuration_json.get("source") == "dynamic"
        else None
    )
    if client is None:
        if record is not None:
            await session.delete(record)
        return cleanup
    if discovery is None:
        raise RuntimeError("OAuth discovery is required to store a client")
    if record is None:
        record = MCPConnectionOAuthClientRecord(
            id=connection.id,
            organization_id=connection.organization_id,
            workspace_id=connection.workspace_id,
            credential_generation=0,
        )
        session.add(record)
    store_client(
        record,
        client,
        source="pre_registered",
        protector=protector,
        resource_url=discovery.resource_url,
        token_endpoint=discovery.token_endpoint,
        scope=discovery.scope,
    )
    return cleanup


class OAuthConfiguration:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        oauth: MCPOAuthClient,
        protector: SecretProtector,
        *,
        public_origin: str | None,
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
            return client_configuration(record) if record is not None else None

    async def discover(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPOAuthDiscovery:
        return self._discovery_projection(await self._discover(actor=actor, connection_id=connection_id))

    async def _discover(self, *, actor: AuthenticatedActor, connection_id: str) -> OAuthDiscovery:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            endpoint = connection.endpoint_url
        try:
            discovered = await self._oauth.discover(endpoint)
        except MCPOAuthError as error:
            unavailable = error.code == "metadata_unavailable"
            raise MCPConnectionError(
                "mcp_oauth_discovery_unavailable" if unavailable else "mcp_oauth_discovery_failed",
                "OAuth discovery is temporarily unavailable."
                if unavailable
                else "OAuth discovery could not be completed.",
                category=ErrorCategory.unavailable if unavailable else ErrorCategory.conflict,
            ) from error
        except httpx2.HTTPError as error:
            raise MCPConnectionError(
                "mcp_oauth_discovery_unavailable",
                "OAuth discovery is temporarily unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        return discovered

    def _discovery_projection(self, discovered: OAuthDiscovery) -> MCPOAuthDiscovery:
        grant_types: list[OAuthGrantType] = []
        if "authorization_code" in discovered.grant_types and self._public_origin is not None:
            grant_types.append("authorization_code")
        if "client_credentials" in discovered.grant_types:
            grant_types.append("client_credentials")
        return MCPOAuthDiscovery(
            issuer_url=discovered.issuer_url,
            redirect_uri=(
                oauth_redirect_uri(self._public_origin, issuer_key(discovered.issuer_url))
                if self._public_origin is not None
                else None
            ),
            token_endpoint_auth_methods_supported=discovered.token_auth_methods,
            grant_types_supported=tuple(grant_types),
            client_registration=discovered.client_registration,
            authorization_response_iss_parameter_supported=(
                discovered.metadata.authorization_response_iss_parameter_supported is True
            ),
        )

    async def configure(
        self, *, actor: AuthenticatedActor, connection_id: str, request: ConfigureMCPOAuthClientRequest
    ) -> MCPConnection:
        # Discovery is outside the transaction; the version fence prevents a
        # concurrent configuration change from being overwritten afterwards.
        client = request.client
        discovered: OAuthDiscovery | None = None
        if client is not None:
            discovered = await self._discover(actor=actor, connection_id=connection_id)
            projection = self._discovery_projection(discovered)
            if not _supports_client(client, projection):
                raise MCPConnectionError(
                    "invalid_oauth_client",
                    "OAuth client must match the discovered issuer and authentication methods.",
                    category=ErrorCategory.invalid_request,
                )
        now = self._clock()
        cleanup_bundle: dict[str, object] | None = None
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            require_version(connection.version, request.expected_version)
            cleanup_bundle = await _replace_client(
                session,
                connection,
                client,
                discovered,
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
            result = connection.to_resource()
        if cleanup_bundle is not None:
            try:
                with fail_after(30):
                    await self._oauth.cleanup_registration_bundle(cleanup_bundle)
            except Exception:
                # The local replacement is authoritative; remote cleanup gets one
                # bounded best-effort attempt and is never retried implicitly.
                pass
        return result
