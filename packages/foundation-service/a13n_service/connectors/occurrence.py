"""At-least-once Trigger ingress with at-most-once root Turn acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from croniter import croniter  # pyright: ignore[reportMissingModuleSource]
from pydantic import JsonValue
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction

from .domain import (
    TRIGGER_OCCURRENCE_ID_PREFIX,
    AcceptedTriggerSource,
    PrincipalRef,
    ScheduleTriggerSource,
    TriggerOccurrenceReceipt,
    bounded_json_object,
)
from .errors import ConnectorError
from .models import TriggerOccurrenceRecord, TriggerRecord
from .provider import (
    ConnectorProviderContext,
    ConnectorProviderEvent,
    ConnectorProviderEventSourceResult,
    invoke_provider,
)
from .registry import ConnectorProviderCatalog
from .template import expand_event_template, expand_schedule_template
from .trigger import TriggerSecretStore


class TriggerTurnAcceptor(Protocol):
    """Common Turn-owned acceptance transaction used by Trigger occurrences."""

    async def prepare_trigger_turn(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        principal: PrincipalRef,
        agent_revision_id: str,
        source: AcceptedTriggerSource,
        input: Mapping[str, JsonValue],
    ) -> object:
        """Authorize, resolve selections, and publish initial state outside a DB session."""
        ...

    async def commit_trigger_turn(
        self,
        session: AsyncSession,
        *,
        prepared: object,
        source: AcceptedTriggerSource,
    ) -> str:
        """Recheck acceptance facts and commit root Session, Thread, Turn, and events."""
        ...


class TriggerIngressService:
    """Verify Provider ingress and submit each unique occurrence to Turn."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        trigger_secrets: TriggerSecretStore,
        turns: TriggerTurnAcceptor,
        *,
        max_webhook_body_bytes: int = 1024 * 1024,
        max_webhook_headers: int = 64,
        max_webhook_header_bytes: int = 32 * 1024,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._trigger_secrets = trigger_secrets
        self._turns = turns
        self._max_webhook_body_bytes = max_webhook_body_bytes
        self._max_webhook_headers = max_webhook_headers
        self._max_webhook_header_bytes = max_webhook_header_bytes

    async def receive_webhook(
        self,
        *,
        trigger_id: str,
        headers: Mapping[str, str],
        body: bytes,
        context: ConnectorProviderContext,
    ) -> tuple[TriggerOccurrenceReceipt, ...]:
        safe_headers = self._bounded_headers(headers)
        if len(body) > self._max_webhook_body_bytes:
            raise ConnectorError("Webhook body is too large.", code="event_payload_invalid")
        snapshot = await self._event_snapshot(trigger_id)
        provider = self._providers.require(snapshot.provider_key)
        if provider.metadata.capabilities.event_delivery != "webhook":
            raise ConnectorError("Trigger Provider does not use webhooks.", code="trigger_source_incompatible")
        secrets = await self._trigger_secrets.read_trigger_secrets(
            organization_id=snapshot.organization_id,
            workspace_id=snapshot.workspace_id,
            trigger_id=trigger_id,
        )
        source_state = ConnectorProviderEventSourceResult(
            provider_state_version=snapshot.provider_state_version,
            provider_state=snapshot.provider_state,
            secrets=secrets,
        )
        try:
            events = await invoke_provider(
                context,
                provider.receive_webhook(
                    context,
                    source=source_state,
                    headers=safe_headers,
                    body=body,
                ),
            )
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("Webhook verification failed.", code="event_signature_invalid") from None
        if len(events) > 100:
            raise ConnectorError("Webhook returned too many events.", code="event_payload_invalid")
        received_at = datetime.now(UTC)
        receipts = []
        for event in events:
            normalized = _bounded_event(event, expected_type=snapshot.event_type)
            receipts.append(await self._accept_event(snapshot, normalized, received_at))
        return tuple(receipts)

    async def accept_due_schedules(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[TriggerOccurrenceReceipt, ...]:
        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        async with short_session(self._sessions) as session:
            trigger_ids = tuple(
                await session.scalars(
                    select(TriggerRecord.id)
                    .where(
                        TriggerRecord.status == "active",
                        TriggerRecord.source_kind == "schedule",
                        TriggerRecord.next_scheduled_at <= current,
                    )
                    .order_by(TriggerRecord.next_scheduled_at, TriggerRecord.id)
                    .limit(limit)
                )
            )
        receipts = []
        for trigger_id in trigger_ids:
            prepared = await self._prepare_due_schedule(trigger_id, current)
            if prepared is None:
                continue
            receipt = await self._accept_due_schedule(trigger_id, current, prepared)
            if receipt is not None:
                receipts.append(receipt)
        return tuple(receipts)

    async def poll_trigger(
        self,
        *,
        trigger_id: str,
        context: ConnectorProviderContext,
    ) -> tuple[TriggerOccurrenceReceipt, ...]:
        snapshot = await self._event_snapshot(trigger_id)
        provider = self._providers.require(snapshot.provider_key)
        if provider.metadata.capabilities.event_delivery != "polling":
            raise ConnectorError("Trigger Provider does not use polling.", code="trigger_source_incompatible")
        secrets = await self._trigger_secrets.read_trigger_secrets(
            organization_id=snapshot.organization_id,
            workspace_id=snapshot.workspace_id,
            trigger_id=trigger_id,
        )
        source_state = ConnectorProviderEventSourceResult(
            provider_state_version=snapshot.provider_state_version,
            provider_state=snapshot.provider_state,
            secrets=secrets,
        )
        events, cursor = await invoke_provider(
            context,
            provider.poll_events(context, source=source_state, cursor=snapshot.event_cursor),
        )
        if len(events) > 100 or (cursor is not None and len(cursor) > 2_000):
            raise ConnectorError("Polling result is too large.", code="event_payload_invalid")
        received_at = datetime.now(UTC)
        receipts = []
        for event in events:
            normalized = _bounded_event(event, expected_type=snapshot.event_type)
            receipts.append(await self._accept_event(snapshot, normalized, received_at))
        async with transaction(self._sessions) as session:
            record = await _get_trigger_for_update(session, trigger_id)
            if record.status != "active" or record.version != snapshot.trigger_version:
                raise ConnectorError("Trigger is not active.", code="trigger_not_active")
            record.event_cursor = cursor
        return tuple(receipts)

    async def _accept_due_schedule(
        self,
        trigger_id: str,
        now: datetime,
        prepared: _PreparedOccurrence,
    ) -> TriggerOccurrenceReceipt | None:
        async with transaction(self._sessions) as session:
            record = await _get_trigger_for_update(session, trigger_id)
            due = record.next_scheduled_at
            if record.status != "active" or record.source_kind != "schedule" or due is None:
                return None
            due = _utc(due)
            if due > now:
                return None
            source = ScheduleTriggerSource(
                type=record.schedule_type,  # type: ignore[arg-type]
                expression=record.schedule_expression,
                timezone=record.schedule_timezone,
                interval_seconds=record.interval_seconds,
            )
            scheduled_for, next_scheduled_at = _latest_due_and_next(source, due, now)
            occurrence_key = _instant_key(scheduled_for)
            if record.version != prepared.trigger_version or occurrence_key != prepared.source.occurrence_key:
                return None
            existing = await _existing_occurrence(session, trigger_id, occurrence_key)
            record.next_scheduled_at = next_scheduled_at
            if existing is not None:
                return _receipt(existing, duplicate=True)
            turn_id = await self._turns.commit_trigger_turn(
                session,
                prepared=prepared.value,
                source=prepared.source,
            )
            occurrence = _new_occurrence(
                record,
                occurrence_key=occurrence_key,
                accepted_turn_id=turn_id,
                received_at=now,
                scheduled_for=scheduled_for,
            )
            session.add(occurrence)
            return _receipt(occurrence, duplicate=False)

    async def _prepare_due_schedule(
        self,
        trigger_id: str,
        now: datetime,
    ) -> _PreparedOccurrence | None:
        async with short_session(self._sessions) as session:
            record = await session.get(TriggerRecord, trigger_id)
        if (
            record is None
            or record.status != "active"
            or record.source_kind != "schedule"
            or record.next_scheduled_at is None
        ):
            return None
        due = _utc(record.next_scheduled_at)
        if due > now:
            return None
        schedule = ScheduleTriggerSource(
            type=record.schedule_type,  # type: ignore[arg-type]
            expression=record.schedule_expression,
            timezone=record.schedule_timezone,
            interval_seconds=record.interval_seconds,
        )
        scheduled_for, _next = _latest_due_and_next(schedule, due, now)
        source = AcceptedTriggerSource(
            trigger_id=record.id,
            trigger_version=record.version,
            source_kind="schedule",
            occurrence_key=_instant_key(scheduled_for),
        )
        input_value = expand_schedule_template(record.input_template, scheduled_at=scheduled_for)
        prepared = await self._turns.prepare_trigger_turn(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            principal=_principal(record),
            agent_revision_id=record.agent_revision_id,
            source=source,
            input=input_value,
        )
        return _PreparedOccurrence(trigger_version=record.version, source=source, value=prepared)

    async def _accept_event(
        self,
        snapshot: _EventTriggerSnapshot,
        event: ConnectorProviderEvent,
        received_at: datetime,
    ) -> TriggerOccurrenceReceipt:
        occurrence_key = event.event_id
        async with short_session(self._sessions) as session:
            existing = await _existing_occurrence(session, snapshot.trigger_id, occurrence_key)
        if existing is not None:
            return _receipt(existing, duplicate=True)
        input_value = expand_event_template(snapshot.input_template, event=event, received_at=received_at)
        accepted_source = AcceptedTriggerSource(
            trigger_id=snapshot.trigger_id,
            trigger_version=snapshot.trigger_version,
            source_kind="connector_event",
            occurrence_key=occurrence_key,
        )
        prepared = await self._turns.prepare_trigger_turn(
            organization_id=snapshot.organization_id,
            workspace_id=snapshot.workspace_id,
            principal=snapshot.principal,
            agent_revision_id=snapshot.agent_revision_id,
            source=accepted_source,
            input=input_value,
        )
        async with transaction(self._sessions) as session:
            record = await _get_trigger_for_update(session, snapshot.trigger_id)
            if (
                record.status != "active"
                or record.version != snapshot.trigger_version
                or record.source_kind != "connector_event"
            ):
                raise ConnectorError("Trigger is not active.", code="trigger_not_active")
            existing = await _existing_occurrence(session, record.id, occurrence_key)
            if existing is not None:
                return _receipt(existing, duplicate=True)
            turn_id = await self._turns.commit_trigger_turn(
                session,
                prepared=prepared,
                source=accepted_source,
            )
            occurrence = _new_occurrence(
                record,
                occurrence_key=occurrence_key,
                accepted_turn_id=turn_id,
                received_at=received_at,
                provider_event_id=event.event_id,
            )
            session.add(occurrence)
            return _receipt(occurrence, duplicate=False)

    async def _event_snapshot(self, trigger_id: str) -> _EventTriggerSnapshot:
        async with short_session(self._sessions) as session:
            record = await session.scalar(
                select(TriggerRecord).where(
                    TriggerRecord.id == trigger_id,
                    TriggerRecord.status == "active",
                    TriggerRecord.source_kind == "connector_event",
                )
            )
        if (
            record is None
            or record.connector_revision_id is None
            or record.event_type is None
            or record.provider_state_version is None
            or record.provider_state is None
        ):
            raise ConnectorError("Trigger is not active.", code="trigger_not_active")
        from .models import ConnectorRevisionRecord

        async with short_session(self._sessions) as session:
            revision = await session.get(ConnectorRevisionRecord, record.connector_revision_id)
        if revision is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        return _EventTriggerSnapshot(
            trigger_id=record.id,
            trigger_version=record.version,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            provider_key=revision.provider_key,
            event_type=record.event_type,
            provider_state_version=record.provider_state_version,
            provider_state=bounded_json_object(record.provider_state, field_name="provider_state"),
            event_cursor=record.event_cursor,
            principal=_principal(record),
            agent_revision_id=record.agent_revision_id,
            input_template=bounded_json_object(record.input_template, field_name="input_template"),
        )

    def _bounded_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        if len(headers) > self._max_webhook_headers:
            raise ConnectorError("Webhook headers are too large.", code="event_payload_invalid")
        detached = {str(key): str(value) for key, value in headers.items()}
        encoded_size = sum(len(key.encode()) + len(value.encode()) for key, value in detached.items())
        if encoded_size > self._max_webhook_header_bytes:
            raise ConnectorError("Webhook headers are too large.", code="event_payload_invalid")
        return detached


class _EventTriggerSnapshot:
    def __init__(
        self,
        *,
        trigger_id: str,
        trigger_version: int,
        organization_id: str,
        workspace_id: str,
        provider_key: str,
        event_type: str,
        provider_state_version: str,
        provider_state: dict[str, JsonValue],
        event_cursor: str | None,
        principal: PrincipalRef,
        agent_revision_id: str,
        input_template: dict[str, JsonValue],
    ) -> None:
        self.trigger_id = trigger_id
        self.trigger_version = trigger_version
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.provider_key = provider_key
        self.event_type = event_type
        self.provider_state_version = provider_state_version
        self.provider_state = provider_state
        self.event_cursor = event_cursor
        self.principal = principal
        self.agent_revision_id = agent_revision_id
        self.input_template = input_template


class _PreparedOccurrence:
    __slots__ = ("source", "trigger_version", "value")

    def __init__(self, *, trigger_version: int, source: AcceptedTriggerSource, value: object) -> None:
        self.trigger_version = trigger_version
        self.source = source
        self.value = value


async def _get_trigger_for_update(session: AsyncSession, trigger_id: str) -> TriggerRecord:
    statement: Select[tuple[TriggerRecord]] = (
        select(TriggerRecord).where(TriggerRecord.id == trigger_id).with_for_update()
    )
    record = await session.scalar(statement)
    if record is None:
        raise ConnectorError("Trigger was not found.", code="not_found")
    return record


async def _existing_occurrence(
    session: AsyncSession,
    trigger_id: str,
    occurrence_key: str,
) -> TriggerOccurrenceRecord | None:
    return await session.scalar(
        select(TriggerOccurrenceRecord).where(
            TriggerOccurrenceRecord.trigger_id == trigger_id,
            TriggerOccurrenceRecord.occurrence_key == occurrence_key,
        )
    )


def _new_occurrence(
    trigger: TriggerRecord,
    *,
    occurrence_key: str,
    accepted_turn_id: str,
    received_at: datetime,
    provider_event_id: str | None = None,
    scheduled_for: datetime | None = None,
) -> TriggerOccurrenceRecord:
    return TriggerOccurrenceRecord(
        id=new_object_id(TRIGGER_OCCURRENCE_ID_PREFIX),
        organization_id=trigger.organization_id,
        workspace_id=trigger.workspace_id,
        trigger_id=trigger.id,
        occurrence_key=occurrence_key,
        provider_event_id=provider_event_id,
        scheduled_for=scheduled_for,
        accepted_turn_id=accepted_turn_id,
        received_at=received_at,
    )


def _receipt(record: TriggerOccurrenceRecord, *, duplicate: bool) -> TriggerOccurrenceReceipt:
    if record.accepted_turn_id is None:
        raise ConnectorError("Trigger occurrence is incomplete.", code="event_source_unavailable")
    return TriggerOccurrenceReceipt(
        trigger_id=record.trigger_id,
        occurrence_key=record.occurrence_key,
        turn_id=record.accepted_turn_id,
        duplicate=duplicate,
    )


def _bounded_event(event: ConnectorProviderEvent, *, expected_type: str) -> ConnectorProviderEvent:
    if not 1 <= len(event.event_id) <= 500 or event.event_type != expected_type:
        raise ConnectorError("Provider event is incompatible.", code="event_payload_invalid")
    occurred_at = event.occurred_at
    if occurred_at is not None and occurred_at.tzinfo is None:
        raise ConnectorError("Provider event time must include a timezone.", code="event_payload_invalid")
    data = bounded_json_object(dict(event.data), field_name="event.data")
    return ConnectorProviderEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        data=data,
        occurred_at=occurred_at.astimezone(UTC) if occurred_at is not None else None,
    )


def _latest_due_and_next(
    source: ScheduleTriggerSource,
    first_due: datetime,
    now: datetime,
) -> tuple[datetime, datetime]:
    if source.type == "interval":
        interval = timedelta(seconds=source.interval_seconds or 0)
        elapsed = max(0, int((now - first_due) // interval))
        latest = first_due + elapsed * interval
        return latest, latest + interval
    zone = ZoneInfo(source.timezone or "")
    local_now = now.astimezone(zone)
    latest = croniter(source.expression or "", local_now + timedelta(microseconds=1)).get_prev(datetime)
    next_value = croniter(source.expression or "", local_now).get_next(datetime)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=zone)
    if next_value.tzinfo is None:
        next_value = next_value.replace(tzinfo=zone)
    return latest.astimezone(UTC), next_value.astimezone(UTC)


def _instant_key(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _principal(record: TriggerRecord) -> PrincipalRef:
    return PrincipalRef(
        principal_type=record.principal_type,  # type: ignore[arg-type]
        principal_id=record.principal_id,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
