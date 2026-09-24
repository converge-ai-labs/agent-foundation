"""Connector accounts of connections: the provider's hosted setup, its completion, the account and its revocation.

Starting the setup is the connection's one operation, and the pending flow keeps the reference of the account it
created. The callback inspects only that account, by its stored reference, and ignores account identifiers the
browser sends. The provider keeps and refreshes the app's own tokens. An account a completed setup replaced is
revoked at the provider as a best effort.
"""

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import anyio
from a13n_harness.providers.connector.contracts import (
    AdapterConnectionStatus,
    ConnectionBinding,
    ConnectorConnectionRuntime,
    ConnectorProviderError,
    SetupCompletionMethod,
    SetupContext,
    SetupStarted,
)
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, now, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.http import require_match
from a13n_service.providers.registry import Registry
from a13n_service.resources.connections.access import ConnectorConnection
from a13n_service.resources.connections.credentials import (
    AccountFlow,
    AccountSecret,
    FlowStart,
    protect,
    reveal,
    store_flow,
)
from a13n_service.resources.connections.operations import (
    Operation,
    claim_operation,
    failure_of,
    invalidate,
    perform,
)
from a13n_service.resources.connections.schemas import AuthorizationResult
from a13n_service.resources.connections.service import authorized
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.connector_providers.catalog import open_connector_provider
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import Providers
from a13n_service.tenancy.authorize import Principal

# What a connector provider that could not be used raises; provider errors carry a safe code.
_PROVIDER_ERRORS = (ConnectorProviderError, TimeoutError, ValueError)


@asynccontextmanager
async def open_account(
    connection: ConnectorConnection,
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
    timeout: float,
) -> AsyncIterator[ConnectorConnectionRuntime]:
    """The connection's external account over its provider's runtime; binding it sends nothing."""
    if connection.credential is None:
        raise conflict("connection", connection.id, "not_authorized")
    account = reveal(
        keys, connection.organization_id, connection.id, "credential", connection.credential, AccountSecret
    )
    async with open_connector_provider(
        connection.provider, keys=keys, registry=registry, policy=policy, settings=settings, timeout=timeout
    ) as runtime:
        yield runtime.connect(_binding(account, connection.config.app))


async def authorize(
    storage: Storage,
    actor: Principal,
    connection: ConnectorConnection,
    start: FlowStart,
    *,
    if_match: str | None,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> AuthorizationResult:
    """Start the provider's hosted setup as the connection's one operation and keep the account it creates."""
    config = connection.config
    async with transaction(storage) as session:
        row = await find_row(session, actor, ConnectionRow, connection.scope, connection.id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        invalidate(row)
        current = await now(session)
        operation = claim_operation(row, "setup", current=current, seconds=settings.operation_seconds)
        audit_row(session, actor, row, "authorize")
    context = SetupContext(
        connector_key=config.app,
        external_user_correlation=connection.id,
        callback_url=f"{start.callback_url}?{urlencode({'state': start.state})}",
    )

    async def send() -> SetupStarted:
        async with open_connector_provider(
            connection.provider,
            keys=keys,
            registry=registry,
            policy=policy,
            settings=settings,
            timeout=settings.operation_seconds,
        ) as runtime:
            started = await runtime.start_setup(setup=config.setup, context=context)
        if started.redirect_url is None:
            raise ConnectorProviderError("hosted_setup_unavailable")
        return started

    def publish(_session: AsyncSession, row: ConnectionRow, started: SetupStarted) -> None:
        flow = AccountFlow(
            principal_id=start.principal_id,
            return_url=start.return_url,
            browser=start.browser,
            account=AccountSecret(account_ref=started.external_ref or started.setup_ref, correlation=connection.id),
            completion=started.completion_method,
            expected_metadata=started.expected_metadata,
        )
        store_flow(keys, row, start, flow, _expiry(current, started, settings))

    try:
        started = await perform(storage, operation, send, publish, seconds=settings.operation_seconds)
    except _PROVIDER_ERRORS as error:
        raise ServiceError(
            "unavailable",
            "The connector provider could not start the account setup",
            {"dependency": f"connector:{connection.type}", "reason": failure_of(error)[1] or "failed"},
        ) from None
    return AuthorizationResult(redirect_url=started.redirect_url, expires_at=_expiry(current, started, settings))


async def complete(
    storage: Storage,
    operation: Operation,
    connection: ConnectorConnection,
    flow: AccountFlow,
    initiator: Principal,
    *,
    session_uri: str | None,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> None:
    """Confirm the account the setup created, for the flow's initiator, as the connection's one operation."""
    context = SetupContext(connector_key=connection.config.app, external_user_correlation=flow.account.correlation)

    async def send() -> AccountSecret:
        async with open_connector_provider(
            connection.provider,
            keys=keys,
            registry=registry,
            policy=policy,
            settings=settings,
            timeout=settings.operation_seconds,
        ) as runtime:
            if flow.completion is SetupCompletionMethod.oauth_verifier:
                if session_uri is None:
                    raise ConnectorProviderError("verifier_session_missing")
                inspection = await runtime.complete_setup(
                    session_uri=session_uri, context=context, expected_external_ref=flow.account.account_ref
                )
            else:
                inspection = await runtime.inspect_setup(setup_ref=flow.account.account_ref, context=context)
        # The provider checked that the inspected account is the one the setup created, for this app and user.
        if inspection is None or inspection.status is not AdapterConnectionStatus.ready:
            raise ConnectorProviderError("account_not_ready")
        if any(inspection.safe_metadata.get(key) != value for key, value in flow.expected_metadata.items()):
            raise ConnectorProviderError("account_metadata_mismatch")
        return flow.account

    replaced: list[AccountSecret] = []

    def publish(session: AsyncSession, row: ConnectionRow, account: AccountSecret) -> None:
        if row.credential is not None:
            replaced.append(reveal(keys, row.organization_id, row.id, "credential", row.credential, AccountSecret))
        row.credential = protect(keys, row.organization_id, row.id, "credential", account)
        authorized(session, row, initiator)

    await perform(storage, operation, send, publish, seconds=settings.operation_seconds)
    for account in replaced:
        if account.account_ref == flow.account.account_ref:
            continue
        # Best effort: a replaced account the provider did not revoke stays there, unused.
        with anyio.move_on_after(settings.operation_seconds), contextlib.suppress(*_PROVIDER_ERRORS, ServiceError):
            async with open_connector_provider(
                connection.provider,
                keys=keys,
                registry=registry,
                policy=policy,
                settings=settings,
                timeout=settings.operation_seconds,
            ) as runtime:
                await runtime.connect(_binding(account, connection.config.app)).revoke(operation_id=operation.id)


async def revoke(
    storage: Storage,
    operation: Operation,
    connection: ConnectorConnection,
    *,
    keys: KeyRing,
    registry: Registry,
    policy: EndpointPolicy,
    settings: Providers,
) -> None:
    """Ask the provider to end the connection's cleared account, as the connection's one operation."""

    async def send() -> None:
        async with open_account(
            connection,
            keys=keys,
            registry=registry,
            policy=policy,
            settings=settings,
            timeout=settings.operation_seconds,
        ) as account:
            await account.revoke(operation_id=operation.id)

    await perform(storage, operation, send, lambda _session, _row, _result: None, seconds=settings.operation_seconds)


def _binding(account: AccountSecret, app: str) -> ConnectionBinding:
    return ConnectionBinding(
        external_user_correlation=account.correlation, external_ref=account.account_ref, connector_key=app
    )


def _expiry(current: datetime, started: SetupStarted, settings: Providers) -> datetime:
    """The flow's own bound, or the provider's when it is sooner."""
    expires_at = current + timedelta(seconds=settings.flow_seconds)
    return expires_at if started.expires_at is None else min(expires_at, started.expires_at.astimezone(UTC))
