"""Bounded association, dispatch and retention owned by the control role."""

from datetime import timedelta
from functools import partial

from sqlalchemy import delete, select, update

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.connector_providers.webhooks import recover
from a13n_service.runs.connector_events.dispatch import dispatch
from a13n_service.runs.connector_events.ingress import bind
from a13n_service.runs.connector_events.tables import EventRow
from a13n_service.runs.connector_events.triggers.operations import maintain
from a13n_service.runs.connector_events.triggers.tables import TriggerRow
from a13n_service.runs.runtime import Runtime


async def associate(runtime: Runtime) -> None:
    config = runtime.settings.connector_events
    async with short_session(runtime.storage) as session:
        rows = (
            await session.execute(
                select(EventRow.id, EventRow.provider_id, EventRow.remote_id)
                .where(EventRow.state == "unmatched", EventRow.due_at <= await now(session))
                .order_by(EventRow.due_at, EventRow.id)
                .limit(config.batch)
            )
        ).all()
    for event_id, provider_id, remote_id in rows:
        async with transaction(runtime.storage) as session:
            trigger = await session.scalar(
                select(TriggerRow)
                .where(TriggerRow.provider_id == provider_id, TriggerRow.remote_id == remote_id)
                .with_for_update(read=True)
            )
            row = await session.scalar(select(EventRow).where(EventRow.id == event_id).with_for_update())
            if row is None or row.state != "unmatched":
                continue
            current = await now(session)
            if trigger is not None:
                try:
                    bind(
                        row,
                        trigger,
                        provisioning=trigger.provisioned_at is not None and row.created_at <= trigger.provisioned_at,
                    )
                except ServiceError as error:
                    row.state, row.failure = "failed", str(error.details.get("reason", "event_source_mismatch"))
            elif row.created_at + timedelta(seconds=config.unmatched_seconds) <= current:
                row.state, row.failure = "failed", "trigger_mapping_unavailable"
            row.due_at = current + timedelta(seconds=5) if row.state == "unmatched" else current


async def cleanup(runtime: Runtime) -> None:
    config = runtime.settings.connector_events
    async with transaction(runtime.storage) as session:
        current = await now(session)
        settled = EventRow.state.not_in(("pending", "unmatched"))
        expired = (
            select(EventRow.id)
            .where(settled, EventRow.created_at < current - timedelta(days=config.retention_days))
            .limit(config.batch)
        )
        await session.execute(delete(EventRow).where(EventRow.id.in_(expired)))
        payloads = (
            select(EventRow.id)
            .where(
                settled,
                EventRow.payload.is_not(None),
                EventRow.created_at < current - timedelta(days=config.payload_days),
            )
            .limit(config.batch)
        )
        await session.execute(update(EventRow).where(EventRow.id.in_(payloads)).values(payload=None))


async def deliver_events(runtime: Runtime) -> None:
    await associate(runtime)
    await dispatch(runtime)
    await cleanup(runtime)


async def manage_subscriptions(runtime: Runtime) -> None:
    await recover(runtime)
    await maintain(runtime)


def delivery_sweep(runtime: Runtime) -> Sweep:
    return Sweep(
        name="connector_events",
        every=runtime.settings.connector_events.scan_seconds,
        run=partial(deliver_events, runtime),
        timeout=60,
    )


def subscription_sweep(runtime: Runtime) -> Sweep:
    return Sweep(
        name="connector_subscriptions",
        every=runtime.settings.providers.operation_scan_seconds,
        run=partial(manage_subscriptions, runtime),
        timeout=120,
    )
