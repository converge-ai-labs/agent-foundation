"""Authorization of a connection's credential, its public callback, and its revocation.

`authorize` dispatches on how the connection authenticates: `oauth` runs its OAuth grant (`oauth.py`) and
`account` its connector provider's hosted setup (`account.py`). Only an OAuth client registered for the
client-credentials grant obtains its credential without a browser. Every other authorization is a browser flow,
which hands a person an authorization URL: only a login session starts one (an API key could hand the URL to
someone else, RFC 9700 consent phishing), and the flow is bound to that browser by a cookie scoped to the
callback, which the callback must bring back. A browser flow stores the hash of a one-use callback state with the
encrypted pending flow; the public callback completes the flow once, as the connection's one operation, for the
principal who started it. The current credential stays usable until a completed flow replaces it, unless a
refresh was in flight: completing a flow ends that refresh as lost, which clears the credential rather than
presenting a possibly rotated refresh token again.

Revocation is offboarding: it reaches a disabled connection and one in an archived workspace.
"""

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing, secret_hash
from a13n_service.infra.db import Storage, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.providers.registry import Registry
from a13n_service.resources.connections import account, oauth
from a13n_service.resources.connections.access import (
    ConnectorConnection,
    McpConnection,
    ResolvedConnection,
    resolve_connection,
)
from a13n_service.resources.connections.credentials import AccountFlow, FlowStart, OAuthFlow, reveal_flow
from a13n_service.resources.connections.operations import (
    Operation,
    claim_operation,
    drop_credential,
    failure_of,
    invalidate,
    refuse,
    supersede,
    wait_for,
)
from a13n_service.resources.connections.schemas import (
    AuthorizationRequest,
    AuthorizationResult,
    CallbackOutcome,
    RevokedConnection,
)
from a13n_service.resources.connections.service import connection_view
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import Providers, Settings
from a13n_service.tenancy.access import Access, principal_for, require_login_session, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

CALLBACK_PATH = "/api/v1/connections/callback"


@dataclass(frozen=True, slots=True)
class Callback:
    """The authorization response the browser brings back; everything but `state` is untrusted input."""

    state: str
    code: str | None = None
    error: str | None = None
    iss: str | None = None
    # A connector's OAuth verifier session, redeemed once.
    session_uri: str | None = None


@dataclass(frozen=True, slots=True)
class CallbackResult:
    return_url: str | None
    outcome: CallbackOutcome


def callback_url(settings: Settings) -> str:
    return settings.server.public_url.rstrip("/") + CALLBACK_PATH


def flow_cookie(connection_id: str, settings: Settings) -> str:
    """The cookie binding a connection's browser flow to the browser that started it; `Secure` over HTTPS."""
    return f"{'__Secure-' if settings.server.https else ''}a13n_flow_{connection_id}"


def return_url_allowed(url: str, settings: Settings) -> bool:
    """A page on one of the Service's public origins, such as the Console, or a configured exact URL."""
    return (
        any(url.startswith(origin + "/") for origin in settings.server.public_origins)
        or url in settings.providers.return_urls
    )


async def authorize_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    body: AuthorizationRequest,
    *,
    browser: str,
    if_match: str | None,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Settings,
) -> AuthorizationResult:
    """Obtain a new credential, through the caller's browser unless the client needs none; it serves every run.

    `browser` is the secret the initiating browser keeps in its flow cookie. A browser flow replaces any pending
    one.
    """
    if body.return_url is not None and not return_url_allowed(body.return_url, settings):
        raise invalid("return_url", "is neither on the Service's origin nor one of the deployment's return URLs")
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        connection = await resolve_connection(session, actor, scope, connection_id, verb="write")
    require_match(if_match, connection.id, connection.version)
    providers = settings.providers
    if isinstance(connection, McpConnection):
        if connection.auth != "oauth":
            raise conflict("connection", connection.id, "no_browser_authorization")
        client = connection.config.oauth
        if client is not None and client.grant_type == "client_credentials":
            return await oauth.authorize_client(
                storage, actor, connection, client, if_match=if_match, keys=keys, policy=policy, settings=providers
            )
    require_login_session(actor)
    start = FlowStart(
        principal_id=actor.id,
        return_url=body.return_url,
        browser=secret_hash(browser),
        state=secrets.token_urlsafe(32),
        callback_url=callback_url(settings),
    )
    if isinstance(connection, ConnectorConnection):
        return await account.authorize(
            storage,
            actor,
            connection,
            start,
            if_match=if_match,
            keys=keys,
            registry=registry,
            policy=policy,
            settings=providers,
        )
    return await oauth.authorize(
        storage, actor, connection, start, if_match=if_match, keys=keys, policy=policy, settings=providers
    )


@dataclass(frozen=True, slots=True)
class _Join:
    """A duplicate callback: the completion another callback claimed decides the outcome."""

    operation_id: str


@dataclass(frozen=True, slots=True)
class _Claim:
    """The completion this callback claimed, and how it completes for the flow's connection."""

    operation: Operation
    complete: Callable[[Operation], Awaitable[None]]


async def complete_authorization(
    storage: Storage,
    access: Access,
    callback: Callback,
    *,
    browsers: Mapping[str, str],
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Settings,
) -> CallbackResult:
    """Complete the flow the state names, once; a duplicate callback joins the completion in progress.

    `browsers` are the request's cookies; one of them binds the flow to the browser that started it.
    """
    providers = settings.providers
    async with transaction(storage) as session:
        row = await session.scalar(
            select(ConnectionRow).where(ConnectionRow.oauth_state_hash == secret_hash(callback.state)).with_for_update()
        )
        if row is None or row.authorization is None or row.authorization_expires_at is None:
            raise invalid("state", "unknown, already used or replaced")
        flow = reveal_flow(keys, row.organization_id, row.id, row.authorization)
        step = await _begin(
            session,
            access,
            row,
            flow,
            callback,
            browser=browsers.get(flow_cookie(row.id, settings)),
            expires_at=row.authorization_expires_at,
            storage=storage,
            redirect_uri=callback_url(settings),
            keys=keys,
            registry=registry,
            policy=policy,
            settings=providers,
        )
    match step:
        case _Join():
            await wait_for(storage, row.id, step.operation_id, timeout=2 * providers.operation_seconds)
            return CallbackResult(flow.return_url, await _current_outcome(storage, row.id))
        case _Claim():
            try:
                await step.complete(step.operation)
            except Exception as error:
                outcome = await _current_outcome(storage, row.id)
                error_code = failure_of(error)[1] or "failed"
                return CallbackResult(flow.return_url, outcome.model_copy(update={"error": error_code}))
            return CallbackResult(flow.return_url, CallbackOutcome(connection_id=row.id, status="ready", error=None))
    return CallbackResult(flow.return_url, step)


async def revoke_connection(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    connection_id: str,
    *,
    if_match: str | None,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Settings,
) -> RevokedConnection:
    """Clear the credential now; ask the remote side to end it where the connection supports that."""
    providers = settings.providers
    operation: Operation | None = None
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write", require_active=False)
        row = await find_row(
            session, actor, ConnectionRow, scope, connection_id, "write", lock=True, require_active=False
        )
        require_match(if_match, row.id, row.version)
        if row.auth == "none":
            raise conflict("connection", row.id, "no_credential")
        remote = await _revocable(session, actor, scope, row, keys)
        invalidate(row)
        if remote is not None:
            operation = claim_operation(row, "revoke", current=await now(session), seconds=providers.operation_seconds)
        drop_credential(row)
        row.failure = None
        row.status = "pending"
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "revoke", {"remote": remote is not None})
    remote_revocation: Literal["revoked", "failed", "skipped"] = "skipped"
    if operation is not None and remote is not None:
        try:
            if isinstance(remote, ConnectorConnection):
                await account.revoke(
                    storage, operation, remote, keys=keys, registry=registry, policy=policy, settings=providers
                )
            else:
                await oauth.revoke(storage, operation, remote, keys=keys, policy=policy, settings=providers)
            remote_revocation = "revoked"
        except Exception:
            # The credential is already cleared; the failed operation is recorded on the connection.
            remote_revocation = "failed"
    async with short_session(storage) as session:
        view = connection_view(await find_row(session, actor, ConnectionRow, scope, connection_id, "read"))
    return RevokedConnection(**dict(view), remote_revocation=remote_revocation)


async def _begin(
    session: AsyncSession,
    access: Access,
    row: ConnectionRow,
    flow: OAuthFlow | AccountFlow,
    callback: Callback,
    *,
    browser: str | None,
    expires_at: datetime,
    storage: Storage,
    redirect_uri: str,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> _Join | _Claim | CallbackOutcome:
    """Join the completion in progress, or refuse the callback, or claim the completion for the flow's initiator.

    A refused callback records its failure and drops the flow; an in-flight refresh continues. A claimed
    completion ends an in-flight refresh as lost, since the completed flow replaces the credential.
    """
    if row.operation_kind == "complete" and row.operation_id is not None:
        return _Join(row.operation_id)
    current = await now(session)
    refusal = callback.error
    if refusal is None and expires_at <= current:
        refusal = "authorization_expired"
    if refusal is None and (browser is None or secret_hash(browser) != flow.browser):
        refusal = "browser_mismatch"
    complete: Callable[[Operation], Awaitable[None]] | None = None
    if refusal is None:
        try:
            initiator, connection = await _initiator_connection(session, access, row, flow.principal_id)
        except ServiceError as error:
            refusal = str(error.details.get("reason", error.code))
        else:
            if isinstance(flow, OAuthFlow) and isinstance(connection, McpConnection):

                async def exchange(operation: Operation) -> None:
                    await oauth.complete(
                        storage,
                        operation,
                        flow,
                        initiator,
                        code=callback.code,
                        iss=callback.iss,
                        redirect_uri=redirect_uri,
                        keys=keys,
                        policy=policy,
                        settings=settings,
                    )

                complete = exchange
            elif isinstance(flow, AccountFlow) and isinstance(connection, ConnectorConnection):

                async def inspect(operation: Operation) -> None:
                    await account.complete(
                        storage,
                        operation,
                        connection,
                        flow,
                        initiator,
                        session_uri=callback.session_uri,
                        keys=keys,
                        registry=registry,
                        policy=policy,
                        settings=settings,
                    )

                complete = inspect
    if complete is None:
        refusal = refusal or "authorization_replaced"
        refuse(row, "complete", _code(refusal))
        return _outcome(row, refusal)
    supersede(row)
    return _Claim(claim_operation(row, "complete", current=current, seconds=settings.operation_seconds), complete)


async def _initiator_connection(
    session: AsyncSession, access: Access, row: ConnectionRow, principal_id: str
) -> tuple[Principal, ResolvedConnection]:
    """The principal who started the flow, and the connection as that principal may still write it."""
    principal = await principal_for(session, access, principal_id)
    scope = await workspace_scope(session, principal, row.workspace_id, "write")
    return principal, await resolve_connection(session, principal, scope, row.id, verb="write")


async def _revocable(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, row: ConnectionRow, keys: KeyRing
) -> ResolvedConnection | None:
    """The connection whose credential its remote side can end; None when revoking only clears it."""
    if row.credential is None or row.auth not in {"oauth", "account"}:
        return None
    try:
        connection = await resolve_connection(session, actor, scope, row.id, verb="write", require_enabled=False)
    except ServiceError:
        return None  # A disabled connector provider is only cleared.
    if isinstance(connection, McpConnection) and not oauth.revocable(keys, connection):
        return None
    return connection


async def _current_outcome(storage: Storage, connection_id: str) -> CallbackOutcome:
    async with short_session(storage) as session:
        row = await session.get(ConnectionRow, connection_id)
    assert row is not None
    return _outcome(row, None if row.status == "ready" else "not_completed")


def _outcome(row: ConnectionRow, error: str | None) -> CallbackOutcome:
    return CallbackOutcome(connection_id=row.id, status=row.status, error=None if error is None else _code(error))


def _code(value: str) -> str:
    """A browser- or provider-supplied error as a bounded code; anything else is replaced, never echoed."""
    return value if len(value) <= 64 and value.replace("_", "").isalnum() and value.isascii() else "failed"
