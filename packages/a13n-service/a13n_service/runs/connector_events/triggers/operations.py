"""Bounded upstream operations with durable creation intent and fenced publication."""

from datetime import timedelta

from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.events import EventType
from a13n_logging import get_logger
from sqlalchemy import select

from a13n_service.infra.db import advisory_lock, now, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connections.credentials import AccountSecret, reveal
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.connector_providers.events import open_events
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.runs.connector_events.triggers.tables import TriggerRow
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)


async def maintain(runtime: Runtime) -> None:
    await check_bindings(runtime)
    settings = runtime.settings.connector_events
    async with short_session(runtime.storage) as session:
        ids = list(
            await session.scalars(
                select(TriggerRow.id)
                .where(
                    TriggerRow.operation_id.is_not(None),
                    TriggerRow.operation_due_at <= await now(session),
                )
                .order_by(TriggerRow.operation_due_at, TriggerRow.id)
                .limit(settings.batch)
            )
        )
    for trigger_id in ids:
        await perform(runtime, trigger_id)


async def perform(runtime: Runtime, trigger_id: str) -> None:
    async with transaction(runtime.storage) as session:
        row = await session.scalar(
            select(TriggerRow).where(TriggerRow.id == trigger_id).with_for_update(skip_locked=True)
        )
        current = await now(session)
        if row is None or row.operation_id is None or row.operation_due_at is None or row.operation_due_at > current:
            return
        if (
            row.operation_kind == "create_unconfirmed"
            or row.operation_attempts >= runtime.settings.connector_events.max_attempts
        ):
            row.failure = row.failure or (
                "subscription_creation_unconfirmed"
                if row.operation_kind == "create_unconfirmed"
                else "upstream_operation_unconfirmed"
            )
            row.operation_id = new_object_id("ctop")
            row.operation_due_at = None
            logger.warning(
                "Connector subscription operation exhausted", extra={"trigger_id": trigger_id, "reason": row.failure}
            )
            return
        claim = new_object_id("ctop")
        row.operation_id = claim
        row.operation_attempts += 1
        row.failure = None
        row.operation_due_at = current + timedelta(seconds=2 * runtime.settings.providers.tool_call_seconds)
        kind, attempt, remote_id = row.operation_kind, row.operation_attempts, row.remote_id
        definition = EventType.model_validate(row.definition)
        parameters, account_id, user_id, provider_id = (
            dict(row.parameters),
            row.account_id,
            row.user_id,
            row.provider_id,
        )
        provider = await read_provider(session, ConnectorProviderRow, provider_id)
        provider_row = await session.get_one(ConnectorProviderRow, provider_id)
        webhook_id = provider_row.event_webhook_id
        url = (provider_row.event_config or {}).get("url")
    try:
        async with open_events(runtime, provider) as adapter:
            webhook = await adapter.webhook()
            if webhook is None or webhook.id != webhook_id or webhook.url != url:
                raise ConnectorProviderError("webhook_identity_changed")
            if kind == "create":
                matches = [
                    item
                    for item in await adapter.instances(account_id, definition.key)
                    if item.config == parameters and item.user_id == user_id
                ]
                if matches:
                    raise ConnectorProviderError("subscription_owned_elsewhere")
                # Persist the non-repeatable boundary before sending the request. Matching
                # remote configuration is not proof that a later instance belongs to us.
                async with transaction(runtime.storage) as session:
                    row = await session.scalar(select(TriggerRow).where(TriggerRow.id == trigger_id).with_for_update())
                    if row is None or row.operation_id != claim:
                        return
                    row.operation_kind = "create_unconfirmed"
                    row.operation_due_at = await now(session) + timedelta(
                        seconds=2 * runtime.settings.providers.tool_call_seconds
                    )
                remote_id = await adapter.upsert(definition, account_id, user_id, parameters)
            elif remote_id is not None:
                await adapter.transition(remote_id, kind or "paused")
        async with transaction(runtime.storage) as session:
            # Receipt timestamps precede mapping publication for every unmatched callback.
            await advisory_lock(session, "connector_events", provider_id)
            row = await session.scalar(select(TriggerRow).where(TriggerRow.id == trigger_id).with_for_update())
            if row is None or row.operation_id != claim:
                return
            row.remote_id = remote_id
            if kind == "create":
                row.provisioned_at = await now(session)
            applied = "enabled" if kind == "create" else kind
            row.failure = None
            if applied != row.state:
                row.operation_id, row.operation_kind = new_object_id("ctop"), row.state
                row.operation_due_at, row.operation_attempts = await now(session), 0
            else:
                row.operation_id, row.operation_kind, row.operation_due_at = None, None, None
            await session.flush()
        logger.info("Connector subscription reconciled", extra={"trigger_id": trigger_id, "provider_id": provider_id})
    except Exception as error:
        reason = error.code if isinstance(error, ConnectorProviderError) else "upstream_operation_unconfirmed"
        async with transaction(runtime.storage) as session:
            row = await session.scalar(select(TriggerRow).where(TriggerRow.id == trigger_id).with_for_update())
            if row is None or row.operation_id != claim:
                return
            if row.operation_kind == "create_unconfirmed":
                reason = "subscription_creation_unconfirmed"
            row.failure = reason
            row.operation_due_at = await now(session) + timedelta(seconds=min(300, 2 ** min(attempt, 8)))
            if reason in {
                "subscription_owned_elsewhere",
                "subscription_creation_unconfirmed",
                "webhook_identity_changed",
            }:
                row.operation_attempts = runtime.settings.connector_events.max_attempts
                row.operation_due_at = None
        logger.warning("Connector subscription operation failed", extra={"trigger_id": trigger_id, "reason": reason})


async def check_bindings(runtime: Runtime) -> None:
    """Retire no identity implicitly; pause subscriptions whose saved account is no longer usable."""
    async with transaction(runtime.storage) as session:
        current = await now(session)
        rows = list(
            await session.scalars(
                select(TriggerRow)
                .where(
                    TriggerRow.state == "enabled",
                    TriggerRow.operation_id.is_(None),
                    TriggerRow.binding_check_at <= current,
                )
                .order_by(TriggerRow.binding_check_at, TriggerRow.id)
                .limit(runtime.settings.connector_events.batch)
                .with_for_update(skip_locked=True)
            )
        )
        if not rows:
            return
        connections = {
            row.id: row
            for row in await session.scalars(
                select(ConnectionRow).where(ConnectionRow.id.in_({row.connection_id for row in rows}))
            )
        }
        for row in rows:
            connection = connections.get(row.connection_id)
            valid = (
                connection is not None
                and connection.enabled
                and connection.status == "ready"
                and connection.credential is not None
            )
            if valid:
                assert connection is not None and connection.credential is not None
                bound = reveal(
                    runtime.keys,
                    row.organization_id,
                    row.connection_id,
                    "credential",
                    connection.credential,
                    AccountSecret,
                )
                valid = (connection.connector_provider_id, bound.account_ref, bound.correlation) == (
                    row.provider_id,
                    row.account_id,
                    row.user_id,
                )
            row.binding_check_at = current + timedelta(seconds=60)
            if not valid:
                row.state, row.failure = "paused", "connection_changed"
                row.operation_id, row.operation_kind, row.operation_due_at = new_object_id("ctop"), "paused", current
                row.operation_attempts = 0
