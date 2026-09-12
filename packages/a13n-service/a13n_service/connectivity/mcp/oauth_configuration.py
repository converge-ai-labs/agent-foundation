"""Connection-owned OAuth application configuration and discovery."""

from __future__ import annotations

import json

import httpx2
from anyio import fail_after
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.iam import AuthenticatedActor
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock

from .domain import (
    ConfigureMCPOAuthClientRequest,
    MCPOAuthClientConfiguration,
    MCPOAuthClientInput,
    MCPOAuthDiscovery,
    MCPOAuthSetup,
    MCPOAuthSetupAction,
    MCPOAuthSetupRequest,
    OAuthClientSource,
    OAuthGrantType,
)
from .errors import MCPConnectionError
from .management import audit, authorize_connection, invalidate_refresh_claim, require_connection, require_version
from .models import MCPAuthorizationRecord, MCPConnectionOAuthClientRecord, MCPConnectionRecord
from .oauth_client import (
    MCPOAuthClient,
    MCPOAuthError,
    OAuthClientContext,
    OAuthDiscovery,
)


def require_oauth(connection: MCPConnectionRecord) -> None:
    if connection.auth_mode != "oauth":
        raise MCPConnectionError("invalid_auth_mode", "Connection does not use OAuth.", category=ErrorCategory.conflict)


def configured_client(record: MCPConnectionOAuthClientRecord, protector: SecretProtector) -> MCPOAuthClientInput:
    return _configured_client(record, _protected_client(record, protector))


def _configured_client(record: MCPConnectionOAuthClientRecord, protected: dict[str, object]) -> MCPOAuthClientInput:
    values = dict(record.configuration_json)
    values.pop("source", None)
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
    protected = _protected_client(record, protector)
    client = _configured_client(record, protected)
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
        and (client.grant_type == "client_credentials" or client.redirect_uri == discovery.redirect_uri)
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
        redirect_uris: tuple[str, ...],
        documentation_urls: dict[str, str],
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._oauth = oauth
        self._protector = protector
        self._public_origin = public_origin
        self._redirect_uris = redirect_uris
        self._documentation_urls = documentation_urls
        self._clock = clock

    async def get(self, *, actor: AuthenticatedActor, connection_id: str) -> MCPOAuthClientConfiguration | None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            return client_configuration(record) if record is not None else None

    async def discover(
        self, *, actor: AuthenticatedActor, connection_id: str, request: MCPOAuthSetupRequest
    ) -> MCPOAuthDiscovery:
        self._validate_redirect_uri(request.redirect_uri)
        return self._discovery_projection(
            await self._discover(actor=actor, connection_id=connection_id), request.redirect_uri
        )

    async def setup(
        self, *, actor: AuthenticatedActor, connection_id: str, request: MCPOAuthSetupRequest
    ) -> MCPOAuthSetup:
        self._validate_redirect_uri(request.redirect_uri)
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_oauth(connection)
            record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            configured = client_configuration(record) if record is not None else None
            documentation_url = self._documentation_urls.get(connection.endpoint_url)
            if connection.ciphertext is not None:
                action = "completed" if connection.status == "ready" else "check_connection"
                return MCPOAuthSetup(
                    next_action=MCPOAuthSetupAction(type=action, documentation_url=documentation_url),
                    client=configured,
                )
            if configured is not None:
                if configured.grant_type == "authorization_code" and configured.redirect_uri != request.redirect_uri:
                    return MCPOAuthSetup(
                        next_action=MCPOAuthSetupAction(
                            type="configure_oauth_client",
                            redirect_uri=request.redirect_uri,
                            issuer_url=configured.issuer_url,
                            token_endpoint_auth_methods=(configured.token_endpoint_auth_method,),
                            grant_types=(configured.grant_type,),
                            documentation_url=documentation_url,
                        ),
                        client=configured,
                    )
                action = (
                    "authenticate_client_credentials"
                    if configured.grant_type == "client_credentials"
                    else "start_authorization"
                )
                return MCPOAuthSetup(
                    next_action=MCPOAuthSetupAction(
                        type=action,
                        redirect_uri=configured.redirect_uri,
                        issuer_url=configured.issuer_url,
                        token_endpoint_auth_methods=(configured.token_endpoint_auth_method,),
                        grant_types=(configured.grant_type,),
                        documentation_url=documentation_url,
                    ),
                    client=configured,
                )
        discovered = await self._discover(actor=actor, connection_id=connection_id)
        projection = self._discovery_projection(discovered, request.redirect_uri)
        automatic = projection.client_registration == "dynamic" or (
            projection.client_registration == "metadata_document" and self._public_origin is not None
        )
        action = (
            "start_authorization"
            if automatic
            and request.redirect_uri is not None
            and "authorization_code" in projection.grant_types_supported
            else "configure_oauth_client"
        )
        return MCPOAuthSetup(
            next_action=MCPOAuthSetupAction(
                type=action,
                redirect_uri=request.redirect_uri,
                issuer_url=projection.issuer_url,
                token_endpoint_auth_methods=projection.token_endpoint_auth_methods_supported,
                grant_types=projection.grant_types_supported,
                client_registration=projection.client_registration,
                documentation_url=documentation_url,
            )
        )

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

    def _discovery_projection(self, discovered: OAuthDiscovery, redirect_uri: str | None) -> MCPOAuthDiscovery:
        grant_types: list[OAuthGrantType] = []
        if "authorization_code" in discovered.grant_types:
            grant_types.append("authorization_code")
        if "client_credentials" in discovered.grant_types:
            grant_types.append("client_credentials")
        return MCPOAuthDiscovery(
            issuer_url=discovered.issuer_url,
            redirect_uri=redirect_uri,
            token_endpoint_auth_methods_supported=discovered.token_auth_methods,
            grant_types_supported=tuple(grant_types),
            client_registration=discovered.client_registration,
            authorization_response_iss_parameter_supported=(
                discovered.metadata.authorization_response_iss_parameter_supported is True
            ),
        )

    async def configure(
        self, *, actor: AuthenticatedActor, connection_id: str, request: ConfigureMCPOAuthClientRequest
    ) -> Connection:
        # Discovery is outside the transaction; the version fence prevents a
        # concurrent configuration change from being overwritten afterwards.
        client = request.client
        discovered: OAuthDiscovery | None = None
        if client is not None:
            self._validate_redirect_uri(client.redirect_uri)
            discovered = await self._discover(actor=actor, connection_id=connection_id)
            projection = self._discovery_projection(discovered, client.redirect_uri)
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
                select(MCPAuthorizationRecord).where(
                    MCPAuthorizationRecord.connection_id == connection_id,
                    MCPAuthorizationRecord.status.in_(("pending", "received", "exchanging")),
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

    def _validate_redirect_uri(self, redirect_uri: str | None) -> None:
        if redirect_uri is not None and redirect_uri not in self._redirect_uris:
            raise MCPConnectionError(
                "invalid_redirect_uri",
                "OAuth redirect URI is not registered for this deployment.",
                category=ErrorCategory.invalid_request,
            )
