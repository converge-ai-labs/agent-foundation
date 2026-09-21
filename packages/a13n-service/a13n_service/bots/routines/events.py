"""Durable event tasks; trusted provider adapters own matching and safe facts."""

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.bots.progress.authority import ProgressUnavailable
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.provider import InboundEvent
from a13n_service.connectivity.native_context import authorized_account
from a13n_service.connectivity.providers.registry import event_subscription_adapters
from a13n_service.connectivity.subscriptions import EventTrigger, require_event_type
from a13n_service.temporal import assume_utc

from .authority import authorize_routine
from .domain import RoutineDefinition
from .models import EventOccurrenceRecord, EventSourceRecord, RoutineRecord


async def resolve_source(
    session: AsyncSession, row: RoutineRecord, trigger: EventTrigger
) -> tuple[AccountRecord, AccountTargetRecord]:
    destination, run, actor = await authorize_routine(session, row)
    target = await session.get(AccountTargetRecord, trigger.source_target_id)
    if target is None or not target.receive_enabled:
        raise ProgressUnavailable("event_target_unavailable")
    existing = await session.get(AccountRecord, target.account_id)
    if (
        existing is None
        or existing.status != "active"
        or existing.deleted_at is not None
        or existing.organization_id != destination.organization_id
        or existing.workspace_id != destination.workspace_id
    ):
        raise ProgressUnavailable("event_source_unavailable")
    account = await authorized_account(
        session,
        actor=actor,
        organization_id=destination.organization_id,
        workspace_id=destination.workspace_id,
        account_id=target.account_id,
    )
    if (
        account.deleted_at is not None
        or not account.receive_enabled
        or account.execution_service_account_id != run.authority_principal_id
        or (target.agent_id or account.default_agent_id) != run.agent_id
    ):
        raise ProgressUnavailable("event_source_not_authorized")
    adapter = event_subscription_adapters().get((account.provider_key, account.provider_config_version))
    if adapter is None or target.target_kind != adapter.target_kind:
        raise ProgressUnavailable("event_source_unsupported")
    try:
        require_event_type(adapter, trigger)
    except ValueError as error:
        raise ProgressUnavailable("event_condition_invalid") from error
    return account, target


def source_snapshot(account: AccountRecord, target: AccountTargetRecord) -> JsonObject:
    return {
        "account_id": account.id,
        "account_name": account.name,
        "account_version": account.version,
        "target_id": target.id,
        "target_version": target.version,
        "provider_key": account.provider_key,
        "target_kind": target.target_kind,
        "external_target_id": target.external_target_id,
    }


async def activate_source(
    session: AsyncSession,
    row: RoutineRecord,
    definition: RoutineDefinition,
    now: datetime,
    *,
    expected: JsonObject | None = None,
) -> None:
    source = await session.get(EventSourceRecord, row.id)
    if definition.event is not None:
        account, target = await resolve_source(session, row, definition.event)
        if expected is not None and source_snapshot(account, target) != expected:
            raise ProgressUnavailable("event_source_changed_since_proposal")
        if source is None:
            source = EventSourceRecord(routine_id=row.id)
            session.add(source)
        source.account_id, source.account_version = account.id, account.version
        source.target_id, source.target_version = target.id, target.version
        source.generation, source.activated_at = secrets.token_urlsafe(24), now
    elif source is not None:
        await session.delete(source)
    # An explicit edit/resume begins a new observation window; never replay a paused backlog.
    await session.execute(
        update(EventOccurrenceRecord)
        .where(
            EventOccurrenceRecord.routine_id == row.id,
            EventOccurrenceRecord.state == "pending",
        )
        .values(state="discarded")
    )


async def authorize_source(session: AsyncSession, row: RoutineRecord) -> EventSourceRecord:
    definition = RoutineDefinition.model_validate(row.definition_json)
    if definition.event is None:
        raise ProgressUnavailable("event_definition_unavailable")
    account, target = await resolve_source(session, row, definition.event)
    source = await session.get(EventSourceRecord, row.id)
    if source is None or (source.account_id, source.account_version, source.target_id, source.target_version) != (
        account.id,
        account.version,
        target.id,
        target.version,
    ):
        raise ProgressUnavailable("event_source_changed")
    return source


async def observe(session: AsyncSession, account: AccountRecord, event: InboundEvent, now: datetime) -> None:
    """Called inside authenticated ingress acceptance; no network or model execution."""
    adapter = event_subscription_adapters().get((account.provider_key, account.provider_config_version))
    if adapter is None:
        return
    rows = (
        await session.scalars(
            select(RoutineRecord)
            .join(
                EventSourceRecord,
                EventSourceRecord.routine_id == RoutineRecord.id,
            )
            .where(EventSourceRecord.account_id == account.id, RoutineRecord.state == "active")
            .with_for_update(of=RoutineRecord)
        )
    ).all()
    for row in rows:
        definition = RoutineDefinition.model_validate(row.definition_json)
        trigger = definition.event
        if trigger is None:
            continue
        source = await session.get(EventSourceRecord, row.id)
        assert source is not None
        target = await session.get(AccountTargetRecord, source.target_id)
        if target is None or target.target_kind != adapter.target_kind:
            continue
        matched = adapter.match(
            trigger,
            event,
            configuration=account.provider_config_json,
            policy=target.provider_policy_json or account.provider_policy_json or {},
        )
        if matched is None or matched.external_target_id != target.external_target_id:
            continue
        activation = assume_utc(source.activated_at)
        if matched.timestamp_precision_seconds:
            activation = activation.replace(microsecond=0)
        if assume_utc(matched.occurred_at) < activation:
            continue
        key = matched.key
        identifier = "rocc_" + hashlib.sha256(f"{row.id}:{source.generation}:{key}".encode()).hexdigest()[:40]
        if await session.get(EventOccurrenceRecord, identifier) is not None:
            continue
        pending = await session.scalar(
            select(func.count())
            .select_from(EventOccurrenceRecord)
            .where(
                EventOccurrenceRecord.routine_id == row.id,
                EventOccurrenceRecord.state == "pending",
            )
        )
        if pending is not None and pending >= 100:
            raise NativeError(
                "admission_capacity_exhausted", "Event task capacity is full.", category=ErrorCategory.unavailable
            )
        session.add(
            EventOccurrenceRecord(
                id=identifier,
                routine_id=row.id,
                generation=source.generation,
                state="pending",
                event_json=matched.facts,
                created_at=now,
            )
        )
        if row.next_run_at is None:
            row.next_run_at = now
        await session.flush()


async def pending_occurrence(session: AsyncSession, row: RoutineRecord) -> EventOccurrenceRecord | None:
    source = await session.get(EventSourceRecord, row.id)
    if source is None:
        return None
    return await session.scalar(
        select(EventOccurrenceRecord)
        .where(
            EventOccurrenceRecord.routine_id == row.id,
            EventOccurrenceRecord.generation == source.generation,
            EventOccurrenceRecord.state == "pending",
        )
        .order_by(EventOccurrenceRecord.created_at, EventOccurrenceRecord.id)
        .limit(1)
    )
