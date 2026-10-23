"""OAuth credentials of MCP connections, and the HTTP client every MCP connection's requests go through.

`authorize` starts the authorization-code flow with PKCE in the browser; a client registered in advance with the
client-credentials grant instead requests its token as the connection's one operation (`authorize_client`). The
callback exchanges the code as one operation too. The credential is the client the grant was made to and changes
only with a new authorization; its tokens are renewed on use without a new connection version: with the refresh
token, or for a client-credentials client by asking again. Renewal is a connection operation: one owner sends it
and concurrent users wait for its result. A lost response that may have rotated the refresh token needs
reauthorization instead of presenting the old token again; a request that was never sent keeps the credential.
A grant a new authorization replaced is revoked as a best effort unless it was made to the same client of the
same issuer, whose revocation could end the new grant too.
"""

import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from mcp.shared.auth import OAuthToken
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, disabled
from a13n_service.infra.http import require_match
from a13n_service.infra.outbound import open_http
from a13n_service.providers.tools.mcp import OAuthSettings
from a13n_service.providers.tools.oauth import (
    OAuthClient,
    OAuthError,
    check_issuer,
    exchange_code,
    renew_access,
    request_client_token,
    revoke_token,
    start_authorization,
)
from a13n_service.resources.connections.access import McpConnection
from a13n_service.resources.connections.credentials import (
    FlowStart,
    HeadersSecret,
    OAuthClientSecret,
    OAuthFlow,
    OAuthTokens,
    protect,
    reveal,
    store_flow,
)
from a13n_service.resources.connections.operations import (
    Operation,
    claim_operation,
    expire_operation,
    invalidate,
    perform,
    replace_flow,
    wait_for,
)
from a13n_service.resources.connections.schemas import AuthorizationResult
from a13n_service.resources.connections.service import authorized
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import Providers
from a13n_service.tenancy.authorize import Principal

# An access token is renewed this long before it expires, so a request never races its expiry.
REFRESH_MARGIN = timedelta(seconds=60)
# How a dynamically registered OAuth client names itself to the authorization server.
CLIENT_NAME = "Agent Foundation"
# What an authorization server that could not be used raises; OAuth errors carry a safe code.
_SERVER_ERRORS = (OAuthError, TimeoutError, ValueError)


@dataclass(frozen=True, slots=True)
class Grant:
    """An OAuth credential: the client the grant was made to and the tokens it issued."""

    client: OAuthClient
    tokens: OAuthTokens
    expires_at: datetime | None


class McpAuthentication:
    """The headers one open MCP connection presents; shared by its concurrent requests."""

    def __init__(
        self, connection: McpConnection, *, storage: Storage, keys: KeyRing, policy: EndpointPolicy, settings: Providers
    ):
        self._connection_id = connection.id
        self._storage, self._keys, self._policy, self._settings = storage, keys, policy, settings
        self._lock = anyio.Lock()
        self._headers: dict[str, str] = {}
        self._grant: Grant | None = None
        if connection.auth == "oauth":
            self._grant = _grant(keys, connection)
        elif connection.credential is not None:
            self._headers = reveal(
                keys, connection.organization_id, connection.id, "credential", connection.credential, HeadersSecret
            ).headers
        elif connection.auth != "none":
            raise conflict("connection", connection.id, "not_authorized")

    async def present(self, request: httpx2.Request) -> None:
        if self._grant is None:
            request.headers.update(self._headers)
            return
        async with self._lock:
            if _due(self._grant, datetime.now(UTC)):
                self._grant = await refresh_oauth(
                    self._storage, self._connection_id, keys=self._keys, policy=self._policy, settings=self._settings
                )
            request.headers["authorization"] = f"Bearer {self._grant.tokens.access_token}"


@asynccontextmanager
async def open_mcp_client(
    connection: McpConnection, *, storage: Storage, keys: KeyRing, policy: EndpointPolicy, settings: Providers
) -> AsyncGenerator[httpx2.AsyncClient]:
    """An HTTP client for the connection's server that presents its credential on every request."""
    authentication = McpAuthentication(connection, storage=storage, keys=keys, policy=policy, settings=settings)
    async with open_http(
        policy,
        timeout=settings.tool_call_seconds,
        max_bytes=settings.response_bytes,
        before_request=authentication.present,
    ) as client:
        yield client


async def refresh_oauth(
    storage: Storage, connection_id: str, *, keys: KeyRing, policy: EndpointPolicy, settings: Providers
) -> Grant:
    """The connection's current grant, renewed first when its access token is about to expire.

    An operation whose owner ran out of time fails first, so a possibly rotated refresh token is never sent again.
    """
    for _ in range(3):
        operation: Operation | None = None
        async with transaction(storage) as session:
            row = await lock(session, ConnectionRow, connection_id)
            if row is None or not row.enabled or row.auth != "oauth":
                raise disabled("connection", connection_id)
            current = await now(session)
            if row.operation_deadline is not None and row.operation_deadline <= current:
                expire_operation(session, row)
            grant = None if row.credential is None else _grant(keys, row)
            outstanding = row.operation_id
            due = grant is not None and _due(grant, current)
            if due and outstanding is None:
                operation = claim_operation(row, "refresh", current=current, seconds=settings.operation_seconds)
        if grant is None:
            raise conflict("connection", connection_id, "reauthorization_required")
        if operation is not None:
            return await _renew(storage, operation, grant, keys=keys, policy=policy, settings=settings)
        if not due or outstanding is None:
            return grant
        await wait_for(storage, connection_id, outstanding, timeout=2 * settings.operation_seconds)
    raise ServiceError("unavailable", "Connection credential is being replaced", {"dependency": "connection"})


async def authorize(
    storage: Storage,
    actor: Principal,
    connection: McpConnection,
    start: FlowStart,
    *,
    if_match: str | None,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> AuthorizationResult:
    """A browser flow for the authorization-code grant; the credential stays usable until the flow completes."""
    oauth = connection.config.oauth or OAuthSettings()
    secret = None if oauth.token_endpoint_auth_method == "none" else _client_secret(connection, keys)
    try:
        with anyio.fail_after(settings.operation_seconds):
            async with _http(policy, settings) as http:
                started = await start_authorization(
                    http,
                    server_url=connection.config.url,
                    settings=oauth,
                    client_secret=secret,
                    redirect_uri=start.callback_url,
                    state=start.state,
                    client_name=CLIENT_NAME,
                )
    except _SERVER_ERRORS as error:
        raise _unavailable(error) from None
    flow = OAuthFlow(
        principal_id=start.principal_id,
        return_url=start.return_url,
        browser=start.browser,
        client=started.client,
        verifier=started.verifier,
        iss_required=started.iss_required,
    )
    async with transaction(storage) as session:
        row = await find_row(session, actor, ConnectionRow, connection.scope, connection.id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        expires_at = await now(session) + timedelta(seconds=settings.flow_seconds)
        replace_flow(row)
        store_flow(keys, row, start, flow, expires_at)
        audit_row(session, actor, row, "authorize")
    return AuthorizationResult(redirect_url=started.url, expires_at=expires_at)


async def authorize_client(
    storage: Storage,
    actor: Principal,
    connection: McpConnection,
    oauth: OAuthSettings,
    *,
    if_match: str | None,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> AuthorizationResult:
    """Request a client-credentials token as the connection's one operation; no browser takes part."""
    secret = _client_secret(connection, keys)
    async with transaction(storage) as session:
        row = await find_row(session, actor, ConnectionRow, connection.scope, connection.id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        invalidate(row)
        operation = claim_operation(row, "complete", current=await now(session), seconds=settings.operation_seconds)
        audit_row(session, actor, row, "authorize")

    async def send() -> Grant:
        async with _http(policy, settings) as http:
            client, token = await request_client_token(
                http, server_url=connection.config.url, settings=oauth, client_secret=secret
            )
        return _granted(client, token)

    try:
        await _obtain(storage, operation, send, actor, keys=keys, policy=policy, settings=settings)
    except _SERVER_ERRORS as error:
        raise _unavailable(error) from None
    return AuthorizationResult(redirect_url=None, expires_at=None)


async def complete(
    storage: Storage,
    operation: Operation,
    flow: OAuthFlow,
    initiator: Principal,
    *,
    code: str | None,
    iss: str | None,
    redirect_uri: str,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> None:
    """Exchange the callback's code for the flow's initiator, as the connection's one operation."""

    async def send() -> Grant:
        check_issuer(flow.client, iss, required=flow.iss_required)
        if code is None:
            raise OAuthError("code_missing")
        async with _http(policy, settings) as http:
            token = await exchange_code(http, flow.client, code=code, verifier=flow.verifier, redirect_uri=redirect_uri)
        return _granted(flow.client, token)

    await _obtain(storage, operation, send, initiator, keys=keys, policy=policy, settings=settings)


def revocable(keys: KeyRing, connection: McpConnection) -> bool:
    """The authorization server of the connection's credential offers token revocation."""
    return _grant(keys, connection).client.revocation_endpoint is not None


async def revoke(
    storage: Storage,
    operation: Operation,
    connection: McpConnection,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> None:
    """Ask the authorization server to end the connection's cleared credential, as its one operation."""
    grant = _grant(keys, connection)
    await perform(
        storage,
        operation,
        lambda: _send_revocation(grant, policy=policy, settings=settings),
        lambda _session, _row, _result: None,
        seconds=settings.operation_seconds,
    )


async def _obtain(
    storage: Storage,
    operation: Operation,
    send: Callable[[], Awaitable[Grant]],
    initiator: Principal,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> None:
    """Obtain a new grant as the claimed operation, store it as the credential and revoke the one it replaced.

    A replaced grant made to the same client of the same issuer stays: a server that keeps one grant per user and
    client would end the new one with it.
    """
    replaced: list[Grant] = []

    def publish(session: AsyncSession, row: ConnectionRow, grant: Grant) -> None:
        if row.credential is not None:
            previous = _grant(keys, row)
            if (previous.client.issuer, previous.client.client_id) != (grant.client.issuer, grant.client.client_id):
                replaced.append(previous)
        row.credential = protect(keys, row.organization_id, row.id, "credential", grant.client)
        row.tokens = protect(keys, row.organization_id, row.id, "tokens", grant.tokens)
        row.expires_at = grant.expires_at
        authorized(session, row, initiator)

    await perform(storage, operation, send, publish, seconds=settings.operation_seconds)
    for grant in replaced:
        # Best effort: a replaced grant the server did not revoke only expires there.
        with anyio.move_on_after(settings.operation_seconds), contextlib.suppress(*_SERVER_ERRORS, ServiceError):
            await _send_revocation(grant, policy=policy, settings=settings)


async def _renew(
    storage: Storage, operation: Operation, grant: Grant, *, keys: KeyRing, policy: EndpointPolicy, settings: Providers
) -> Grant:
    async def send() -> Grant:
        async with _http(policy, settings) as http:
            token = await renew_access(http, grant.client, grant.tokens.refresh_token)
        return _granted(grant.client, token, refresh_token=grant.tokens.refresh_token)

    def publish(_session: AsyncSession, row: ConnectionRow, renewed: Grant) -> None:
        row.tokens = protect(keys, row.organization_id, row.id, "tokens", renewed.tokens)
        row.expires_at = renewed.expires_at

    try:
        return await perform(storage, operation, send, publish, seconds=settings.operation_seconds)
    except _SERVER_ERRORS as error:
        raise _unavailable(error) from None


async def _send_revocation(grant: Grant, *, policy: EndpointPolicy, settings: Providers) -> None:
    async with _http(policy, settings) as http:
        await revoke_token(http, grant.client, grant.tokens.refresh_token or grant.tokens.access_token)


def _due(grant: Grant, current: datetime) -> bool:
    """The access token is about to expire and renews without a person."""
    renewable = grant.tokens.refresh_token is not None or grant.client.grant_type == "client_credentials"
    return renewable and grant.expires_at is not None and grant.expires_at - REFRESH_MARGIN <= current


def _granted(client: OAuthClient, token: OAuthToken, *, refresh_token: str | None = None) -> Grant:
    """The grant a token response makes; a response without a refresh token keeps `refresh_token`."""
    tokens = OAuthTokens(access_token=token.access_token, refresh_token=token.refresh_token or refresh_token)
    expires_at = None if token.expires_in is None else datetime.now(UTC) + timedelta(seconds=token.expires_in)
    return Grant(client, tokens, expires_at)


def _grant(keys: KeyRing, connection: McpConnection | ConnectionRow) -> Grant:
    """The OAuth credential of a resolved or locked connection; the `tokens` check pairs tokens with a client."""
    if connection.credential is None or connection.tokens is None:
        raise conflict("connection", connection.id, "not_authorized")
    client = reveal(keys, connection.organization_id, connection.id, "credential", connection.credential, OAuthClient)
    tokens = reveal(keys, connection.organization_id, connection.id, "tokens", connection.tokens, OAuthTokens)
    return Grant(client, tokens, connection.expires_at)


def _client_secret(connection: McpConnection, keys: KeyRing) -> str:
    """The secret of a client registered in advance that authenticates with one."""
    if connection.client_secret is None:
        raise conflict("connection", connection.id, "client_secret_missing")
    envelope = connection.client_secret
    return reveal(keys, connection.organization_id, connection.id, "client_secret", envelope, OAuthClientSecret).value


@asynccontextmanager
async def _http(policy: EndpointPolicy, settings: Providers) -> AsyncGenerator[httpx2.AsyncClient]:
    """The client of one OAuth operation. Waiting for a connection and each step of making one (resolving, TCP,
    TLS) take at most a fifth of the operation's bound, so a request that was never sent fails as such before the
    operation's deadline would leave its outcome unknown."""
    seconds = settings.operation_seconds
    async with open_http(policy, timeout=seconds, max_bytes=settings.response_bytes) as http:
        http.timeout = httpx2.Timeout(seconds, connect=seconds / 5, pool=seconds / 5)
        yield http


def _unavailable(error: Exception) -> ServiceError:
    code = error.code if isinstance(error, OAuthError) else "authorization_server_unavailable"
    return ServiceError(
        "unavailable", "The authorization server could not be used", {"dependency": "oauth", "reason": code}
    )
