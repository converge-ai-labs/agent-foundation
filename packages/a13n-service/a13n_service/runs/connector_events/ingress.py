"""Signature verification and one short, durable acceptance transaction."""

from collections.abc import Mapping
from datetime import UTC, datetime

from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.connector.events import Event
from a13n_logging import get_logger
from sqlalchemy import func, select

from a13n_service.infra.db import advisory_lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found, rate_limited
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connector_providers.webhooks import secret
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.runs.connector_events import metrics
from a13n_service.runs.connector_events.tables import EventRow
from a13n_service.runs.connector_events.triggers.service import digest, schema_valid
from a13n_service.runs.connector_events.triggers.tables import TriggerRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import refuse_archived
from a13n_service.tenancy.tables import WorkspaceRow

logger = get_logger(__name__)


def bind(event: EventRow, trigger: TriggerRow, *, provisioning: bool = False) -> None:
    if (event.account_id, event.user_id, event.event_type) != (trigger.account_id, trigger.user_id, trigger.event_type):
        raise conflict("connector_event", event.id, "event_source_mismatch")
    event.trigger_id = trigger.id
    target = trigger.initial_target if provisioning else trigger.target
    event.snapshot = {
        "target": target,
        "connection_id": trigger.connection_id,
        "account_id": trigger.account_id,
        "trigger_version": 1 if provisioning else trigger.version,
    }
    schema = trigger.definition.get("payload_schema")
    if (
        provisioning
        and trigger.provisioning_stopped_at is not None
        and event.created_at >= trigger.provisioning_stopped_at
    ):
        event.state, event.failure = "ignored", "trigger_stopped_during_creation"
    elif not provisioning and trigger.state != "enabled":
        event.state, event.failure = "ignored", f"trigger_{trigger.state}"
    elif schema is not None and not schema_valid(schema, event.payload):
        event.state, event.failure = "failed", "event_payload_schema_mismatch"
    else:
        event.state = "stored" if target["mode"] == "store_only" else "pending"


async def receive(runtime: Runtime, provider_id: str, body: bytes, headers: Mapping[str, str]) -> str:
    limits = runtime.settings.connector_events
    if len(body) > limits.body_bytes:
        raise ServiceError("payload_too_large", "Connector event exceeds its byte limit", {"limit": limits.body_bytes})
    async with short_session(runtime.storage) as session:
        provider = await session.get(ConnectorProviderRow, provider_id)
        if provider is None:
            raise not_found("connector_provider", provider_id)
        signing_secret, version = secret(runtime, provider), provider.version
        definition = runtime.registry.get("connector", provider.type)
    if not isinstance(definition, ConnectorProviderDefinition) or definition.verify_event is None:
        raise conflict("connector_provider", provider_id, "events_unsupported")
    try:
        event = definition.verify_event(body, headers, signing_secret, now=datetime.now(UTC))
    except ConnectorProviderError:
        metrics.rejected.add(1, {"reason": "invalid_signature_or_payload"})
        raise ServiceError("unauthenticated", "Invalid connector event signature or payload") from None
    return await accept(runtime, provider_id, version, event)


async def accept(runtime: Runtime, provider_id: str, provider_version: int, event: Event) -> str:
    content_digest = digest(event.model_dump(mode="json"))
    limits = runtime.settings.connector_events
    async with transaction(runtime.storage) as session:
        # Serialize the quota and duplicate check per provider, without changing its configuration row.
        await advisory_lock(session, "connector_events", provider_id)
        provider = await session.scalar(
            select(ConnectorProviderRow).where(ConnectorProviderRow.id == provider_id).with_for_update(read=True)
        )
        if provider is None or provider.version != provider_version:
            raise ServiceError("unavailable", "Connector callback configuration changed; retry the event")
        existing = await session.scalar(
            select(EventRow).where(EventRow.provider_id == provider_id, EventRow.source_id == event.source_id)
        )
        if existing is not None:
            if existing.digest != content_digest:
                metrics.rejected.add(1, {"reason": "source_id_reused"})
                logger.warning(
                    "Conflicting connector event identity", extra={"provider_id": provider_id, "event_id": existing.id}
                )
                raise conflict("connector_event", existing.id, "source_id_reused")
            metrics.duplicates.add(1)
            return existing.id
        refuse_archived(await session.get_one(WorkspaceRow, provider.workspace_id))
        trigger = await session.scalar(
            select(TriggerRow)
            .where(TriggerRow.provider_id == provider_id, TriggerRow.remote_id == event.instance_id)
            .with_for_update(read=True)
        )
        count = await session.scalar(
            select(func.count())
            .select_from(EventRow)
            .where(EventRow.provider_id == provider_id, EventRow.state.in_(("pending", "unmatched")))
        )
        if (count or 0) >= limits.pending_per_provider:
            raise rate_limited("Connector event backlog is full", 5)
        if trigger is None:
            unmatched = await session.scalar(
                select(func.count())
                .select_from(EventRow)
                .where(EventRow.provider_id == provider_id, EventRow.state == "unmatched")
            )
            if (unmatched or 0) >= limits.unmatched_per_provider:
                raise rate_limited("Unmatched connector event backlog is full", 5)
        current = await now(session)
        row = EventRow(
            id=new_object_id("cevt"),
            organization_id=provider.organization_id,
            workspace_id=provider.workspace_id,
            provider_id=provider_id,
            source_id=event.source_id,
            remote_id=event.instance_id,
            account_id=event.account_id,
            user_id=event.user_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=event.payload,
            digest=content_digest,
            state="unmatched",
            created_at=current,
            attempts=0,
            due_at=current,
        )
        if trigger is not None:
            bind(row, trigger)
        session.add(row)
        await session.flush()
        event_id = row.id
    metrics.accepted.add(1, {"state": row.state})
    logger.info("Connector event accepted", extra={"provider_id": provider_id, "event_id": event_id})
    return event_id
