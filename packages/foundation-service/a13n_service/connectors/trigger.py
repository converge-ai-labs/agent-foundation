"""Trigger resource lifecycle and Provider event-source orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter  # pyright: ignore[reportMissingModuleSource]
from pydantic import JsonValue
from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction

from .domain import (
    TRIGGER_ID_PREFIX,
    ConnectorEventTriggerSource,
    CreateTrigger,
    PrincipalRef,
    ScheduleTriggerSource,
    Trigger,
    TriggerSource,
    UpdateTrigger,
    bounded_json_object,
)
from .errors import ConnectorError
from .models import (
    ConnectionRecord,
    ConnectorRecord,
    ConnectorRevisionRecord,
    TriggerOccurrenceRecord,
    TriggerRecord,
)
from .provider import (
    ConnectorProvider,
    ConnectorProviderConnection,
    ConnectorProviderContext,
    ConnectorProviderEventSourceResult,
    ConnectorProviderSecret,
    invoke_provider,
)
from .registry import ConnectorProviderCatalog
from .service import ConnectionSecretStore
from .template import validate_trigger_input_template


class AgentTriggerTargetValidator(Protocol):
    """Agent-owned validation used before committing a Trigger definition."""

    async def validate_trigger_target(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        agent_revision_id: str,
        input_template: Mapping[str, JsonValue],
        principal: PrincipalRef,
    ) -> None: ...


class TriggerSecretStore(Protocol):
    """Trigger-owned managed Secret operations supplied by the Secret domain."""

    async def replace_trigger_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        secrets: Sequence[ConnectorProviderSecret],
        actor: PrincipalRef,
    ) -> None: ...

    async def read_trigger_secrets(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
    ) -> tuple[ConnectorProviderSecret, ...]: ...

    async def delete_trigger_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        actor: PrincipalRef,
    ) -> None: ...


class TriggerService:
    """Own Trigger definitions while delegating Agent and Secret authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderCatalog,
        connection_secrets: ConnectionSecretStore,
        trigger_secrets: TriggerSecretStore,
        agent_targets: AgentTriggerTargetValidator,
        *,
        minimum_interval_seconds: int = 60,
    ) -> None:
        if minimum_interval_seconds < 1:
            raise ValueError("minimum_interval_seconds must be positive")
        self._sessions = sessions
        self._providers = providers
        self._connection_secrets = connection_secrets
        self._trigger_secrets = trigger_secrets
        self._agent_targets = agent_targets
        self._minimum_interval_seconds = minimum_interval_seconds

    async def create(self, request: CreateTrigger) -> Trigger:
        input_template = validate_trigger_input_template(
            request.input_template,
            source_kind=request.source.kind,
        )
        await self._agent_targets.validate_trigger_target(
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            agent_revision_id=request.agent_revision_id,
            input_template=input_template,
            principal=request.principal_ref,
        )
        source = await self._validate_source(
            request.organization_id,
            request.workspace_id,
            request.source,
        )
        now = datetime.now(UTC)
        record = TriggerRecord(
            id=new_object_id(TRIGGER_ID_PREFIX),
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
            name=request.name,
            description=request.description,
            principal_type=request.principal_ref.principal_type,
            principal_id=request.principal_ref.principal_id,
            agent_revision_id=request.agent_revision_id,
            input_template=input_template,
            provider_state_version=None,
            provider_state=None,
            event_cursor=None,
            lifecycle_operation_id=None,
            cleanup_pending=False,
            status="disabled",
            status_reason=None,
            version=1,
            created_by_type=request.created_by.principal_type,
            created_by_id=request.created_by.principal_id,
            created_at=now,
            updated_at=now,
            **_source_columns(source),
        )
        async with transaction(self._sessions) as session:
            session.add(record)
        return _trigger(record)

    async def get(self, organization_id: str, workspace_id: str, trigger_id: str) -> Trigger:
        async with short_session(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id)
        return _trigger(record)

    async def list(
        self,
        organization_id: str,
        workspace_id: str,
        *,
        limit: int = 100,
    ) -> tuple[Trigger, ...]:
        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        statement = (
            select(TriggerRecord)
            .where(
                TriggerRecord.organization_id == organization_id,
                TriggerRecord.workspace_id == workspace_id,
            )
            .order_by(TriggerRecord.updated_at.desc(), TriggerRecord.id.desc())
            .limit(limit)
        )
        async with short_session(self._sessions) as session:
            records = tuple((await session.scalars(statement)).all())
        return tuple(_trigger(record) for record in records)

    async def update(
        self,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        request: UpdateTrigger,
    ) -> Trigger:
        async with short_session(self._sessions) as session:
            existing = await _get_trigger(session, organization_id, workspace_id, trigger_id)
            existing_status = existing.status
            current_source = _trigger_source(existing)
            current_principal = _principal(existing.principal_type, existing.principal_id)
            current_agent_revision_id = existing.agent_revision_id
            current_input_template = existing.input_template
        if request.changes_behavior and existing_status != "disabled":
            raise ConnectorError(
                "Trigger behavior can change only while disabled.",
                code="trigger_not_disabled",
            )
        source = (
            request.source if "source" in request.model_fields_set and request.source is not None else current_source
        )
        principal = (
            request.principal_ref
            if "principal_ref" in request.model_fields_set and request.principal_ref is not None
            else current_principal
        )
        agent_revision_id = (
            request.agent_revision_id
            if "agent_revision_id" in request.model_fields_set and request.agent_revision_id is not None
            else current_agent_revision_id
        )
        input_template = (
            validate_trigger_input_template(
                request.input_template,
                source_kind=source.kind,
            )
            if "input_template" in request.model_fields_set and request.input_template is not None
            else current_input_template
        )
        if "source" in request.model_fields_set and "input_template" not in request.model_fields_set:
            input_template = validate_trigger_input_template(
                input_template,
                source_kind=source.kind,
            )
        if request.changes_behavior:
            await self._agent_targets.validate_trigger_target(
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_revision_id=agent_revision_id,
                input_template=input_template,
                principal=principal,
            )
            source = await self._validate_source(organization_id, workspace_id, source)

        async with transaction(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            _require_trigger_version(record, request.expected_version)
            if request.changes_behavior and record.status != "disabled":
                raise ConnectorError(
                    "Trigger behavior can change only while disabled.",
                    code="trigger_not_disabled",
                )
            changed = False
            if "name" in request.model_fields_set and request.name is not None and request.name != record.name:
                record.name = request.name
                changed = True
            if "description" in request.model_fields_set and request.description != record.description:
                record.description = request.description
                changed = True
            if request.changes_behavior:
                source_columns = _source_columns(source)
                for field_name, value in source_columns.items():
                    if getattr(record, field_name) != value:
                        setattr(record, field_name, value)
                        changed = True
                if record.principal_type != principal.principal_type or record.principal_id != principal.principal_id:
                    record.principal_type = principal.principal_type
                    record.principal_id = principal.principal_id
                    changed = True
                if record.agent_revision_id != agent_revision_id:
                    record.agent_revision_id = agent_revision_id
                    changed = True
                if record.input_template != input_template:
                    record.input_template = input_template
                    changed = True
            if changed:
                _advance_trigger(record)
        return _trigger(record)

    async def enable(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        callback_url: str | None,
        actor: PrincipalRef,
    ) -> Trigger:
        async with transaction(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            _require_trigger_version(record, expected_version)
            if record.status not in {"disabled", "failed"}:
                raise ConnectorError("Trigger cannot be enabled from its current state.", code="trigger_not_disabled")
            source = _trigger_source(record)
            if isinstance(source, ScheduleTriggerSource):
                record.status = "active"
                record.status_reason = None
                record.next_scheduled_at = _next_schedule(source, datetime.now(UTC))
                _advance_trigger(record)
                return _trigger(record)
            record.status = "activating"
            record.status_reason = None
            record.lifecycle_operation_id = context.operation_id
            _advance_trigger(record)
            activating_version = record.version

        try:
            provider, provider_connection, revision = await self._event_runtime(
                organization_id,
                workspace_id,
                source,
            )
            if provider.metadata.capabilities.event_delivery == "webhook" and callback_url is None:
                raise ConnectorError("Webhook Provider requires a callback URL.", code="invalid_request")
            result = await invoke_provider(
                context,
                provider.start_event_source(
                    context,
                    provider_config_version=revision.provider_config_version,
                    config=revision.config,
                    connection=provider_connection,
                    event_type=source.event_type,
                    provider_event_config_version=source.provider_event_config_version,
                    event_config=source.config,
                    callback_url=(callback_url if provider.metadata.capabilities.event_delivery == "webhook" else None),
                ),
            )
            provider_state = bounded_json_object(dict(result.provider_state), field_name="provider_state")
        except Exception:
            async with transaction(self._sessions) as session:
                failed = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
                if failed.status == "activating" and failed.version == activating_version:
                    failed.status = "failed"
                    failed.status_reason = "event_source_unavailable"
                    _advance_trigger(failed)
            return _trigger(failed)

        async with transaction(self._sessions) as session:
            active = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            if active.status != "activating" or active.version != activating_version:
                raise ConnectorError("Trigger activation was superseded.", code="version_conflict")
            await self._trigger_secrets.replace_trigger_secrets(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                trigger_id=trigger_id,
                secrets=result.secrets,
                actor=actor,
            )
            active.provider_state_version = result.provider_state_version
            active.provider_state = provider_state
            active.status = "active"
            active.status_reason = None
            active.cleanup_pending = False
            _advance_trigger(active)
        return _trigger(active)

    async def disable(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        actor: PrincipalRef,
    ) -> Trigger:
        async with transaction(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            _require_trigger_version(record, expected_version)
            if record.status == "disabled":
                return _trigger(record)
            source = _trigger_source(record)
            cleanup_required = isinstance(source, ConnectorEventTriggerSource) and record.provider_state is not None
            record.status = "disabled"
            record.status_reason = None
            record.next_scheduled_at = None
            record.cleanup_pending = cleanup_required
            record.lifecycle_operation_id = context.operation_id if cleanup_required else None
            _advance_trigger(record)
            disabled_version = record.version
            state_version = record.provider_state_version
            state = record.provider_state
        if not cleanup_required or state_version is None or state is None:
            return _trigger(record)
        if not isinstance(source, ConnectorEventTriggerSource):
            raise AssertionError("event-source cleanup requires a Connector event source")
        trigger_secrets = await self._trigger_secrets.read_trigger_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            trigger_id=trigger_id,
        )
        source_state = ConnectorProviderEventSourceResult(
            provider_state_version=state_version,
            provider_state=state,
            secrets=trigger_secrets,
        )
        try:
            provider = self._providers.require(
                (await self._event_revision(organization_id, workspace_id, source)).provider_key
            )
            await invoke_provider(context, provider.stop_event_source(context, source=source_state))
        except Exception:
            return _trigger(record)
        async with transaction(self._sessions) as session:
            cleaned = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            if cleaned.status == "disabled" and cleaned.version == disabled_version:
                await self._trigger_secrets.delete_trigger_secrets(
                    session,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    trigger_id=trigger_id,
                    actor=actor,
                )
                cleaned.provider_state_version = None
                cleaned.provider_state = None
                cleaned.event_cursor = None
                cleaned.cleanup_pending = False
                cleaned.lifecycle_operation_id = None
                _advance_trigger(cleaned)
        return _trigger(cleaned)

    async def delete(
        self,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        *,
        expected_version: int,
    ) -> None:
        async with transaction(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
            _require_trigger_version(record, expected_version)
            occurrence_count = await session.scalar(
                select(func.count())
                .select_from(TriggerOccurrenceRecord)
                .where(TriggerOccurrenceRecord.trigger_id == trigger_id)
            )
            if record.status != "disabled" or record.cleanup_pending or occurrence_count:
                raise ConnectorError("Trigger cannot be deleted while retained or active.", code="trigger_in_use")
            await session.execute(delete(TriggerRecord).where(TriggerRecord.id == trigger_id))

    async def renew_event_source(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        actor: PrincipalRef,
    ) -> Trigger:
        record, source, source_state = await self._load_source_operation(
            organization_id,
            workspace_id,
            trigger_id,
            expected_version=expected_version,
            statuses={"active"},
            require_state=True,
        )
        if source_state is None:
            raise AssertionError("required event source state is missing")
        provider = self._providers.require(
            (await self._event_revision(organization_id, workspace_id, source)).provider_key
        )
        try:
            result = await invoke_provider(context, provider.renew_event_source(context, source=source_state))
        except Exception:
            async with transaction(self._sessions) as session:
                failed = await _get_trigger(
                    session,
                    organization_id,
                    workspace_id,
                    trigger_id,
                    for_update=True,
                )
                if failed.status == "active" and failed.version == record.version:
                    failed.status = "failed"
                    failed.status_reason = "event_source_unavailable"
                    _advance_trigger(failed)
            return _trigger(failed)
        return await self._commit_source_result(
            record,
            result,
            expected_statuses={"active"},
            actor=actor,
        )

    async def reconcile_event_source(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        expected_version: int,
        context: ConnectorProviderContext,
        actor: PrincipalRef,
    ) -> Trigger:
        record, source, source_state = await self._load_source_operation(
            organization_id,
            workspace_id,
            trigger_id,
            expected_version=expected_version,
            statuses={"activating", "active", "failed", "disabled"},
            require_state=False,
        )
        if record.status == "disabled":
            if not record.cleanup_pending or source_state is None:
                return _trigger(record)
            provider = self._providers.require(
                (await self._event_revision(organization_id, workspace_id, source)).provider_key
            )
            await invoke_provider(context, provider.stop_event_source(context, source=source_state))
            async with transaction(self._sessions) as session:
                current = await _get_trigger(session, organization_id, workspace_id, trigger_id, for_update=True)
                _require_trigger_version(current, expected_version)
                if current.status != "disabled" or not current.cleanup_pending:
                    raise ConnectorError("Trigger cleanup was superseded.", code="version_conflict")
                await self._trigger_secrets.delete_trigger_secrets(
                    session,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    trigger_id=trigger_id,
                    actor=actor,
                )
                current.provider_state_version = None
                current.provider_state = None
                current.event_cursor = None
                current.cleanup_pending = False
                current.lifecycle_operation_id = None
                _advance_trigger(current)
            return _trigger(current)
        provider, connection, revision = await self._event_runtime(organization_id, workspace_id, source)
        result = await invoke_provider(
            context,
            provider.reconcile_event_source(
                context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=connection,
                event_type=source.event_type,
                provider_event_config_version=source.provider_event_config_version,
                event_config=source.config,
                source=source_state,
            ),
        )
        return await self._commit_source_result(
            record,
            result,
            expected_statuses={record.status},
            actor=actor,
        )

    async def _load_source_operation(
        self,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        *,
        expected_version: int,
        statuses: set[str],
        require_state: bool,
    ) -> tuple[TriggerRecord, ConnectorEventTriggerSource, ConnectorProviderEventSourceResult | None]:
        async with short_session(self._sessions) as session:
            record = await _get_trigger(session, organization_id, workspace_id, trigger_id)
        _require_trigger_version(record, expected_version)
        if record.status not in statuses:
            raise ConnectorError("Trigger source operation is not allowed.", code="trigger_source_incompatible")
        source = _trigger_source(record)
        if not isinstance(source, ConnectorEventTriggerSource):
            raise ConnectorError("Schedule Trigger has no Provider event source.", code="trigger_source_incompatible")
        source_state = None
        if record.provider_state_version is not None and record.provider_state is not None:
            secrets = await self._trigger_secrets.read_trigger_secrets(
                organization_id=organization_id,
                workspace_id=workspace_id,
                trigger_id=trigger_id,
            )
            source_state = ConnectorProviderEventSourceResult(
                provider_state_version=record.provider_state_version,
                provider_state=record.provider_state,
                secrets=secrets,
            )
        if require_state and source_state is None:
            raise ConnectorError("Trigger event source state is unavailable.", code="event_source_unavailable")
        return record, source, source_state

    async def _commit_source_result(
        self,
        snapshot: TriggerRecord,
        result: ConnectorProviderEventSourceResult,
        *,
        expected_statuses: set[str],
        actor: PrincipalRef,
    ) -> Trigger:
        provider_state = bounded_json_object(dict(result.provider_state), field_name="provider_state")
        async with transaction(self._sessions) as session:
            current = await _get_trigger(
                session,
                snapshot.organization_id,
                snapshot.workspace_id,
                snapshot.id,
                for_update=True,
            )
            if current.version != snapshot.version or current.status not in expected_statuses:
                raise ConnectorError("Trigger source operation was superseded.", code="version_conflict")
            await self._trigger_secrets.replace_trigger_secrets(
                session,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                trigger_id=current.id,
                secrets=result.secrets,
                actor=actor,
            )
            current.provider_state_version = result.provider_state_version
            current.provider_state = provider_state
            current.status = "active"
            current.status_reason = None
            current.cleanup_pending = False
            current.lifecycle_operation_id = None
            _advance_trigger(current)
        return _trigger(current)

    async def _validate_source(
        self,
        organization_id: str,
        workspace_id: str,
        source: TriggerSource,
    ) -> TriggerSource:
        if isinstance(source, ScheduleTriggerSource):
            if source.type == "interval" and source.interval_seconds is not None:
                if source.interval_seconds < self._minimum_interval_seconds:
                    raise ConnectorError(
                        "Trigger interval is below deployment policy.",
                        code="invalid_request",
                        details={"field": "interval_seconds"},
                    )
            if source.type == "cron":
                try:
                    zone = ZoneInfo(source.timezone or "")
                    croniter(source.expression or "", datetime.now(zone)).get_next(datetime)
                except (ValueError, ZoneInfoNotFoundError):
                    raise ConnectorError("Trigger cron schedule is invalid.", code="invalid_request") from None
            return source
        provider, connection, revision = await self._event_runtime(organization_id, workspace_id, source)
        event_config = bounded_json_object(source.config, field_name="event_config")
        try:
            provider.validate_event_config(
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=connection,
                event_type=source.event_type,
                provider_event_config_version=source.provider_event_config_version,
                event_config=event_config,
            )
        except Exception:
            raise ConnectorError(
                "Connector Provider rejected the event configuration.",
                code="trigger_source_incompatible",
            ) from None
        return source.model_copy(update={"config": event_config})

    async def _event_runtime(
        self,
        organization_id: str,
        workspace_id: str,
        source: ConnectorEventTriggerSource,
    ) -> tuple[ConnectorProvider, ConnectorProviderConnection, ConnectorRevisionRecord]:
        revision = await self._event_revision(organization_id, workspace_id, source)
        async with short_session(self._sessions) as session:
            connector = await session.scalar(
                select(ConnectorRecord).where(
                    ConnectorRecord.id == revision.connector_id,
                    ConnectorRecord.organization_id == organization_id,
                    ConnectorRecord.workspace_id == workspace_id,
                    ConnectorRecord.enabled.is_(True),
                )
            )
            connection = await session.scalar(
                select(ConnectionRecord).where(
                    ConnectionRecord.id == source.connection_id,
                    ConnectionRecord.organization_id == organization_id,
                    ConnectionRecord.workspace_id == workspace_id,
                    ConnectionRecord.connector_id == revision.connector_id,
                    ConnectionRecord.provider_key == revision.provider_key,
                    ConnectionRecord.status == "active",
                )
            )
        if connector is None:
            raise ConnectorError("Connector is disabled or unavailable.", code="connector_disabled")
        if connection is None:
            raise ConnectorError("Trigger Connection is unavailable.", code="connection_required")
        secrets = await self._connection_secrets.read_connection_secrets(
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_id=connection.id,
        )
        provider = self._providers.require(revision.provider_key)
        provider_connection = ConnectorProviderConnection(
            provider_state_version=connection.provider_state_version,
            provider_state=connection.provider_state,
            secrets=secrets,
        )
        provider.validate_connection(
            provider_config_version=revision.provider_config_version,
            config=revision.config,
            connection=provider_connection,
        )
        return provider, provider_connection, revision

    async def _event_revision(
        self,
        organization_id: str,
        workspace_id: str,
        source: ConnectorEventTriggerSource,
    ) -> ConnectorRevisionRecord:
        async with short_session(self._sessions) as session:
            revision = await session.scalar(
                select(ConnectorRevisionRecord).where(
                    ConnectorRevisionRecord.id == source.connector_revision_id,
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                )
            )
        if revision is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        return revision


async def _get_trigger(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    trigger_id: str,
    *,
    for_update: bool = False,
) -> TriggerRecord:
    statement: Select[tuple[TriggerRecord]] = select(TriggerRecord).where(
        TriggerRecord.id == trigger_id,
        TriggerRecord.organization_id == organization_id,
        TriggerRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise ConnectorError("Trigger was not found.", code="not_found")
    return record


def _source_columns(source: TriggerSource) -> dict[str, object]:
    if isinstance(source, ScheduleTriggerSource):
        return {
            "source_kind": "schedule",
            "schedule_type": source.type,
            "schedule_expression": source.expression,
            "schedule_timezone": source.timezone,
            "interval_seconds": source.interval_seconds,
            "next_scheduled_at": None,
            "connector_revision_id": None,
            "connection_id": None,
            "event_type": None,
            "provider_event_config_version": None,
            "event_config": None,
        }
    return {
        "source_kind": "connector_event",
        "schedule_type": None,
        "schedule_expression": None,
        "schedule_timezone": None,
        "interval_seconds": None,
        "next_scheduled_at": None,
        "connector_revision_id": source.connector_revision_id,
        "connection_id": source.connection_id,
        "event_type": source.event_type,
        "provider_event_config_version": source.provider_event_config_version,
        "event_config": source.config,
    }


def _trigger_source(record: TriggerRecord) -> TriggerSource:
    if record.source_kind == "schedule":
        return ScheduleTriggerSource(
            type=record.schedule_type,  # type: ignore[arg-type]
            expression=record.schedule_expression,
            timezone=record.schedule_timezone,
            interval_seconds=record.interval_seconds,
        )
    return ConnectorEventTriggerSource(
        connector_revision_id=record.connector_revision_id or "",
        connection_id=record.connection_id or "",
        event_type=record.event_type or "",
        provider_event_config_version=record.provider_event_config_version or "",
        config=record.event_config or {},
    )


def _trigger(record: TriggerRecord) -> Trigger:
    return Trigger(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        principal_ref=_principal(record.principal_type, record.principal_id),
        agent_revision_id=record.agent_revision_id,
        source=_trigger_source(record),
        input_template=record.input_template,
        status=record.status,  # type: ignore[arg-type]
        status_reason=record.status_reason,
        version=record.version,
        created_by=_principal(record.created_by_type, record.created_by_id),
        created_at=_utc(record.created_at),
        updated_at=_utc(record.updated_at),
    )


def _next_schedule(source: ScheduleTriggerSource, now: datetime) -> datetime:
    if source.type == "interval":
        return now + timedelta(seconds=source.interval_seconds or 0)
    zone = ZoneInfo(source.timezone or "")
    local_now = now.astimezone(zone)
    candidate = croniter(source.expression or "", local_now).get_next(datetime)
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=zone)
    return candidate.astimezone(UTC)


def _require_trigger_version(record: TriggerRecord, expected: int) -> None:
    if record.version != expected:
        raise ConnectorError(
            "Trigger version does not match expected_version.",
            code="version_conflict",
            details={"current_version": record.version},
        )


def _advance_trigger(record: TriggerRecord) -> None:
    record.version += 1
    record.updated_at = datetime.now(UTC)


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=principal_type, principal_id=principal_id)  # type: ignore[arg-type]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
