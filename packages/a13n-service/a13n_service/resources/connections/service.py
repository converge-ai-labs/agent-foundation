"""Connection management, and the checks threads and agent revisions make against connections.

A connection's identity for authentication is its server or app and how it authenticates; changing any of
it drops the credential and any pending authorization, so another endpoint never inherits a credential. An
OAuth client's secret belongs to its server and client ID and is dropped when either changes.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import JsonValue, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.errors import disabled, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.registry import Registry
from a13n_service.providers.tools.mcp import McpConfig
from a13n_service.resources.connections.credentials import HeadersSecret, OAuthClientSecret, protect
from a13n_service.resources.connections.operations import drop_credential, drop_flow, invalidate
from a13n_service.resources.connections.schemas import (
    BearerCredential,
    Connection,
    ConnectionConfig,
    ConnectionCreate,
    ConnectionFailure,
    ConnectionPage,
    ConnectionSelection,
    ConnectionTestOutcome,
    ConnectionUpdate,
    ConnectorConfig,
    EnteredCredential,
    HeadersCredential,
    exposed_tools,
)
from a13n_service.resources.connections.tables import ConnectionAuth, ConnectionRow, ConnectionStatus
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope


def connection_view(row: ConnectionRow) -> Connection:
    return Connection(
        id=row.id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        type=row.type,
        name=row.name,
        config=parse_config(row.type, row.config),
        auth=row.auth,
        connector_provider_id=row.connector_provider_id,
        status=row.status,
        failure=None if row.failure is None else ConnectionFailure.model_validate(row.failure),
        credential_configured=row.credential is not None,
        client_secret_configured=row.client_secret is not None,
        authorization_pending=row.authorization_expires_at is not None
        and row.authorization_expires_at > datetime.now(UTC),
        last_test=None if row.last_test is None else ConnectionTestOutcome.model_validate(row.last_test),
        enabled=row.enabled,
        version=row.version,
        created_by_id=row.created_by_id,
        updated_by_id=row.updated_by_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def parse_config(connection_type: str, config: Mapping[str, JsonValue]) -> ConnectionConfig:
    return McpConfig.model_validate(config) if connection_type == "mcp" else ConnectorConfig.model_validate(config)


async def create_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: ConnectionCreate,
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
) -> Connection:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        if (body.type == "mcp") != (body.connector_provider_id is None):
            raise invalid("connector_provider_id", "required exactly for connector types")
        config = await _validated_config(
            session, actor, scope, body.type, body.connector_provider_id, body.config, registry=registry, policy=policy
        )
        _check_auth(body.type, body.auth, config, body.credential, body.client_secret, replaces_credential=True)
        row = ConnectionRow(
            id=new_object_id("conn"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            type=body.type,
            name=body.name,
            config=config.model_dump(mode="json"),
            auth=body.auth,
            connector_provider_id=body.connector_provider_id,
            status=_unauthorized_status(body.auth),
            enabled=True,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        if body.credential is not None:
            _store_entered(keys, row, body.credential)
        if body.client_secret is not None:
            _store_client_secret(keys, row, body.client_secret)
        session.add(row)
        await session.flush()
        audit_row(session, actor, row, "create")
        return connection_view(row)


async def get_connection(storage: Storage, actor: Principal, workspace_id: str, connection_id: str) -> Connection:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return connection_view(await find_row(session, actor, ConnectionRow, scope, connection_id, "read"))


async def list_connections(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> ConnectionPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(ConnectionRow).where(ConnectionRow.workspace_id == scope.workspace_id),
            ConnectionRow.id,
            kind="connections",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
    return ConnectionPage(items=[connection_view(row) for row in rows], next_cursor=next_cursor)


async def update_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    body: ConnectionUpdate,
    *,
    if_match: str | None,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
) -> Connection:
    replaces_credential = "credential" in body.model_fields_set
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, ConnectionRow, scope, connection_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        stored = parse_config(row.type, row.config)
        config = stored
        if body.config is not None:
            config = await _validated_config(
                session,
                actor,
                scope,
                row.type,
                row.connector_provider_id,
                body.config,
                registry=registry,
                policy=policy,
            )
        auth = body.auth or row.auth
        _check_auth(
            row.type, auth, config, body.credential, body.client_secret, replaces_credential=replaces_credential
        )
        rebound = _identity(row.auth, stored) != _identity(auth, config)
        keeps_secret = "client_secret" not in body.model_fields_set and _client(row.auth, stored) == _client(
            auth, config
        )
        changed = assign(row, {**given(body, "name", "auth", "enabled"), "config": config.model_dump(mode="json")})
        if replaces_credential and (body.credential is not None or row.credential is not None):
            changed.append("credential")
        if body.client_secret is not None or (row.client_secret is not None and not keeps_secret):
            changed.append("client_secret")
        if rebound or {"credential", "client_secret"} & set(changed):
            invalidate(row)
            drop_credential(row)
            row.failure = None
            row.status = _unauthorized_status(auth)
        if body.credential is not None:
            _store_entered(keys, row, body.credential)
        if "client_secret" in changed:
            row.client_secret = None
            if body.client_secret is not None:
                _store_client_secret(keys, row, body.client_secret)
        if record_update(session, actor, row, changed):
            await session.flush()
        return connection_view(row)


async def validate_caller_headers(session: AsyncSession, workspace_id: str, headers: dict[str, dict[str, str]]) -> None:
    """Thread caller headers name enabled MCP connections of the workspace and never their authentication.

    Names are already normalized by `normalize_headers`, which refuses `authorization` for every connection.
    """
    if not headers:
        return
    rows = {
        row.id: row
        for row in await session.scalars(
            select(ConnectionRow).where(
                ConnectionRow.workspace_id == workspace_id,
                ConnectionRow.id.in_(headers),
                ConnectionRow.type == "mcp",
                ConnectionRow.enabled,
            )
        )
    }
    for connection_id, values in sorted(headers.items()):
        row = rows.get(connection_id)
        if row is None:
            raise invalid("mcp_headers", f"{connection_id} is not an enabled MCP connection of this workspace")
        check_caller_headers(connection_id, McpConfig.model_validate(row.config), values)


def check_caller_headers(connection_id: str, config: McpConfig, headers: Mapping[str, str]) -> None:
    """Caller headers never name the connection's own credential headers."""
    if set(headers) & set(config.headers):
        raise invalid("mcp_headers", f"headers for {connection_id} collide with its authentication headers")


async def validate_selection(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, selection: ConnectionSelection
) -> str:
    """The type of a connection an agent revision of the workspace may select with these tools.

    The connection must be enabled. Deferred loading is tool search over an MCP server's listing; connector
    actions are always loaded.
    """
    row = await find_row(session, actor, ConnectionRow, scope, selection.connection_id, "read")
    if not row.enabled:
        raise disabled("connection", row.id)
    if selection.defer_loading and row.type != "mcp":
        raise invalid("defer_loading", "applies only to MCP connections")
    exposed = exposed_tools(parse_config(row.type, row.config))
    if selection.tools is not None and exposed is not None and not set(selection.tools) <= set(exposed):
        raise invalid("connections", f"{row.id} does not expose every selected tool")
    return row.type


async def _validated_config(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    connection_type: str,
    provider_id: str | None,
    config: ConnectionConfig,
    *,
    registry: Registry,
    policy: EndpointPolicy,
) -> ConnectionConfig:
    """The configuration to store: a permitted MCP endpoint, or an app setup the connector provider accepts."""
    if isinstance(config, McpConfig):
        if connection_type != "mcp":
            raise invalid("config", "a connector type needs an app configuration")
        try:
            url = await policy.validate(config.url)
        except ValueError as error:
            raise invalid("config.url", str(error)) from None
        return config.model_copy(update={"url": url})
    if connection_type == "mcp" or provider_id is None:
        raise invalid("config", "an MCP connection needs a server configuration")
    provider = await resolve_provider(session, actor, ConnectorProviderRow, scope, provider_id, verb="run")
    if provider.type != connection_type:
        raise invalid("type", f"connector provider {provider_id} serves {provider.type}")
    try:
        setup = registry.get("connector", connection_type).validate_setup(
            config.setup, connector_key=config.app, configuration=dict(provider.config)
        )
    except ValidationError as error:
        locations = ", ".join(".".join(map(str, item["loc"])) or "value" for item in error.errors()[:5])
        raise invalid("config.setup", f"invalid fields: {locations}") from None
    except ValueError:
        raise invalid("config.setup", "rejected by the connector provider type") from None
    return config.model_copy(update={"setup": setup})


def _check_auth(
    connection_type: str,
    auth: ConnectionAuth,
    config: ConnectionConfig,
    credential: EnteredCredential | None,
    client_secret: SecretStr | None,
    *,
    replaces_credential: bool,
) -> None:
    if connection_type != "mcp":
        if auth != "account":
            raise invalid("auth", "connector connections authenticate through the provider account")
    elif auth == "account":
        raise invalid("auth", "account authentication needs a connector provider")
    if isinstance(config, McpConfig):
        if (auth == "headers") != bool(config.headers):
            raise invalid("config.headers", "names the credential headers exactly for headers authentication")
        if config.oauth is not None and auth != "oauth":
            raise invalid("config.oauth", "applies only to oauth authentication")
    if client_secret is not None and _client(auth, config) is None:
        raise invalid("client_secret", "applies only to an OAuth client that authenticates with a secret")
    if not replaces_credential or credential is None:
        return
    if auth == "bearer" and isinstance(credential, BearerCredential):
        return
    if (
        auth == "headers"
        and isinstance(credential, HeadersCredential)
        and isinstance(config, McpConfig)
        and set(credential.headers) == set(config.headers)
    ):
        return
    raise invalid("credential", f"does not match {auth} authentication and its configured headers")


def _store_entered(keys: KeyRing, row: ConnectionRow, credential: EnteredCredential) -> None:
    if isinstance(credential, BearerCredential):
        headers = {"authorization": "Bearer " + credential.token.get_secret_value()}
    else:
        headers = {name: value.get_secret_value() for name, value in credential.headers.items()}
    row.credential = protect(keys, row.organization_id, row.id, "credential", HeadersSecret(headers=headers))
    row.status = "ready"


def _store_client_secret(keys: KeyRing, row: ConnectionRow, secret: SecretStr) -> None:
    value = OAuthClientSecret(value=secret.get_secret_value())
    row.client_secret = protect(keys, row.organization_id, row.id, "client_secret", value)


def _client(auth: ConnectionAuth, config: ConnectionConfig) -> tuple[str, str] | None:
    """The server and client ID a client secret belongs to; None when the connection authenticates no client."""
    if auth != "oauth" or not isinstance(config, McpConfig) or config.oauth is None:
        return None
    oauth = config.oauth
    if oauth.client_id is None or oauth.token_endpoint_auth_method == "none":
        return None
    return config.url, oauth.client_id


def _identity(auth: ConnectionAuth, config: ConnectionConfig) -> tuple[object, ...]:
    """What a credential is bound to; tool selection is not part of it."""
    if isinstance(config, McpConfig):
        return auth, config.url, config.headers, config.oauth
    return auth, config.app, config.setup


def _unauthorized_status(auth: ConnectionAuth) -> ConnectionStatus:
    return "ready" if auth == "none" else "pending"


def authorized(session: AsyncSession, row: ConnectionRow, initiator: Principal) -> None:
    """A credential an authorization obtained replaced any pending flow; the change is its initiator's."""
    drop_flow(row)
    row.status = "ready"
    row.updated_by_id = initiator.id
    audit_row(session, initiator, row, "authorization.complete")
