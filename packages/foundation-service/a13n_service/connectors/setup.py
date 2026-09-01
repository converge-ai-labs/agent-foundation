"""Expiring one-shot Connection setup orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import JsonValue
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction

from .domain import (
    CONNECTION_ID_PREFIX,
    CONNECTION_SETUP_ID_PREFIX,
    Connection,
    ConnectionSetupReceipt,
    PrincipalRef,
    StartConnectionSetup,
    bounded_json_object,
)
from .errors import ConnectorError
from .models import ConnectionRecord, ConnectionSetupRecord, ConnectorRecord, ConnectorRevisionRecord
from .operations import ConnectorProviderOperations, provider_operations
from .provider import ConnectorProviderConnection, ConnectorProviderConnectionResult, ConnectorProviderContext
from .registry import ConnectorProviderCatalog
from .service import ConnectionSecretStore, _connection


class ConnectionSetupStateProtector(Protocol):
    """Secret-domain encryption and authenticated callback-state operations."""

    def encrypt_setup_state(self, setup_id: str, value: Mapping[str, JsonValue]) -> bytes: ...

    def decrypt_setup_state(self, setup_id: str, ciphertext: bytes) -> dict[str, JsonValue]: ...

    def issue_callback_state(self, setup_id: str, provider_key: str, expires_at: datetime) -> str: ...

    def resolve_callback_state(self, state: str, provider_key: str) -> str: ...

    def digest_setup_request(self, value: bytes) -> str: ...


class ConnectionSetupService:
    """Create one Connection only after a complete Provider setup result."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: ConnectorProviderOperations | ConnectorProviderCatalog,
        secrets: ConnectionSecretStore,
        protector: ConnectionSetupStateProtector,
        *,
        lifetime_seconds: int = 600,
    ) -> None:
        if not 60 <= lifetime_seconds <= 3_600:
            raise ValueError("Connection setup lifetime must be between 60 and 3600 seconds")
        self._sessions = sessions
        self._providers = provider_operations(providers)
        self._secrets = secrets
        self._protector = protector
        self._lifetime = timedelta(seconds=lifetime_seconds)

    async def start(
        self,
        request: StartConnectionSetup,
        *,
        context: ConnectorProviderContext,
        callback_url: str | None = None,
    ) -> ConnectionSetupReceipt:
        setup_input = bounded_json_object(request.input, field_name="input")
        request_digest = self._protector.digest_setup_request(
            json.dumps(
                request.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        if not 1 <= len(request_digest) <= 128:
            raise ConnectorError("Connection setup digest is invalid.", code="connection_incompatible")
        existing = await self._find_operation(context.operation_id)
        if existing is not None:
            _require_operation_replay(existing, request=request, request_digest=request_digest)
            if existing.status == "completed":
                return await self._completed_receipt(existing)
            if existing.status == "pending":
                return _receipt(existing, connection=None)
            if existing.status != "starting":
                raise ConnectorError("Connection setup cannot be retried.", code="connection_incompatible")
        revision, _connector = await self._load_target(
            request.organization_id,
            request.workspace_id,
            request.connector_revision_id,
        )
        target_connection = await self._load_reauthorization_target(request, revision)
        metadata = await self._providers.metadata(revision.provider_key)
        if not metadata.capabilities.connections:
            raise ConnectorError("Connector Provider supports no Connections.", code="connection_incompatible")
        if request.setup_mode not in metadata.connection_setup_modes:
            raise ConnectorError("Connection setup mode is unsupported.", code="invalid_request")
        now = datetime.now(UTC)
        expires_at = now + self._lifetime
        if existing is None:
            record = ConnectionSetupRecord(
                id=new_object_id(CONNECTION_SETUP_ID_PREFIX),
                organization_id=request.organization_id,
                workspace_id=request.workspace_id,
                connector_revision_id=revision.id,
                target_connection_id=target_connection.id if target_connection is not None else None,
                provider_key=revision.provider_key,
                principal_type=request.principal_ref.principal_type if request.principal_ref else None,
                principal_id=request.principal_ref.principal_id if request.principal_ref else None,
                name=request.name,
                setup_mode=request.setup_mode,
                initiated_by_type=request.actor.principal_type,
                initiated_by_id=request.actor.principal_id,
                operation_id=context.operation_id,
                request_digest=request_digest,
                continuation_ciphertext=None,
                next_action=None,
                status="starting",
                status_reason=None,
                connection_id=None,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
            try:
                async with transaction(self._sessions) as session:
                    session.add(record)
                    await session.flush()
            except IntegrityError:
                existing = await self._find_operation(context.operation_id)
                if existing is None:
                    raise
                _require_operation_replay(existing, request=request, request_digest=request_digest)
                if existing.status == "completed":
                    return await self._completed_receipt(existing)
                if existing.status == "pending":
                    return _receipt(existing, connection=None)
                if existing.status != "starting":
                    raise ConnectorError(
                        "Connection setup cannot be retried.", code="connection_incompatible"
                    ) from None
                record = existing
                expires_at = _utc(existing.expires_at)
        else:
            record = existing
            expires_at = _utc(existing.expires_at)
        callback_state = (
            self._protector.issue_callback_state(record.id, revision.provider_key, expires_at)
            if callback_url is not None
            else None
        )
        try:
            result = await self._providers.start_connection(
                revision.provider_key,
                context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                setup_mode=request.setup_mode,
                input=setup_input,
                callback_url=callback_url,
                callback_state=callback_state,
            )
        except ConnectorError:
            await self._fail(record.id, "connection_setup_failed")
            raise
        except Exception:
            await self._fail(record.id, "connection_setup_failed")
            raise ConnectorError("Connection setup failed.", code="connection_incompatible") from None
        if result.completed:
            if result.connection is None:
                raise AssertionError("Provider setup result invariant was not enforced")
            return await self._complete_result(record.id, result.connection)
        if result.continuation_state is None or result.next_action is None:
            await self._fail(record.id, "connection_setup_failed")
            raise ConnectorError("Provider returned an incomplete setup step.", code="connection_incompatible")
        continuation = bounded_json_object(dict(result.continuation_state), field_name="continuation_state")
        next_action = bounded_json_object(dict(result.next_action), field_name="next_action")
        ciphertext = self._protector.encrypt_setup_state(record.id, continuation)
        if len(ciphertext) > 128 * 1024:
            await self._fail(record.id, "connection_setup_failed")
            raise ConnectorError("Encrypted setup state is too large.", code="connection_incompatible")
        async with transaction(self._sessions) as session:
            pending = await _get_setup(session, record.id, for_update=True)
            if pending.status != "starting":
                raise ConnectorError("Connection setup was superseded.", code="version_conflict")
            pending.continuation_ciphertext = ciphertext
            pending.next_action = next_action
            pending.status = "pending"
            pending.updated_at = datetime.now(UTC)
        return _receipt(pending, connection=None)

    async def complete(
        self,
        *,
        setup_id: str,
        input: Mapping[str, JsonValue],
        context: ConnectorProviderContext,
        actor: PrincipalRef | None,
        provider_key: str | None = None,
    ) -> ConnectionSetupReceipt:
        setup_input = bounded_json_object(dict(input), field_name="input")
        async with short_session(self._sessions) as session:
            snapshot = await _get_setup(session, setup_id)
            _require_setup_identity(snapshot, actor=actor, provider_key=provider_key)
            if snapshot.status == "completed":
                return await self._completed_receipt(snapshot)
            _require_setup_eligible(snapshot, actor=actor, provider_key=provider_key)
            ciphertext = snapshot.continuation_ciphertext
        if ciphertext is None:
            raise ConnectorError("Connection setup has no continuation state.", code="connection_incompatible")
        continuation = self._protector.decrypt_setup_state(setup_id, ciphertext)
        async with transaction(self._sessions) as session:
            current = await _get_setup(session, setup_id, for_update=True)
            _require_setup_eligible(current, actor=actor, provider_key=provider_key)
            if current.status == "pending":
                current.status = "completing"
                current.updated_at = datetime.now(UTC)
        async with short_session(self._sessions) as session:
            revision = await session.get(ConnectorRevisionRecord, current.connector_revision_id)
        if revision is None:
            await self._fail(setup_id, "connection_setup_failed")
            raise ConnectorError("Connector revision was not found.", code="not_found")
        effective_context = ConnectorProviderContext(
            operation_id=f"{current.operation_id}:complete",
            deadline=context.deadline,
        )
        try:
            result = await self._providers.complete_connection(
                current.provider_key,
                effective_context,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                continuation_state=continuation,
                input=setup_input,
            )
        except ConnectorError:
            await self._fail(setup_id, "connection_setup_failed")
            raise
        except Exception:
            await self._fail(setup_id, "connection_setup_failed")
            raise ConnectorError("Connection setup failed.", code="connection_incompatible") from None
        return await self._complete_result(setup_id, result)

    async def complete_callback(
        self,
        *,
        provider_key: str,
        state: str,
        input: Mapping[str, JsonValue],
        context: ConnectorProviderContext,
    ) -> ConnectionSetupReceipt:
        try:
            setup_id = self._protector.resolve_callback_state(state, provider_key)
        except Exception:
            raise ConnectorError("Connection callback state is invalid.", code="invalid_request") from None
        return await self.complete(
            setup_id=setup_id,
            input=input,
            context=context,
            actor=None,
            provider_key=provider_key,
        )

    async def expire(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Destroy provisional ciphertext for bounded expired setup operations."""

        if not 1 <= limit <= 100:
            raise ConnectorError("limit must be between 1 and 100", code="invalid_request")
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        async with short_session(self._sessions) as session:
            setup_ids = tuple(
                await session.scalars(
                    select(ConnectionSetupRecord.id)
                    .where(
                        ConnectionSetupRecord.status.in_(("starting", "pending", "completing")),
                        ConnectionSetupRecord.expires_at <= current_time,
                    )
                    .order_by(ConnectionSetupRecord.expires_at, ConnectionSetupRecord.id)
                    .limit(limit)
                )
            )
        expired = 0
        for setup_id in setup_ids:
            async with transaction(self._sessions) as session:
                record = await _get_setup(session, setup_id, for_update=True)
                if record.status not in {"starting", "pending", "completing"} or _utc(record.expires_at) > current_time:
                    continue
                record.status = "failed"
                record.status_reason = "connection_setup_expired"
                record.continuation_ciphertext = None
                record.next_action = None
                record.updated_at = current_time
                expired += 1
        return expired

    async def _complete_result(
        self,
        setup_id: str,
        result: ConnectorProviderConnectionResult,
    ) -> ConnectionSetupReceipt:
        async with short_session(self._sessions) as session:
            setup = await _get_setup(session, setup_id)
            revision = await session.get(ConnectorRevisionRecord, setup.connector_revision_id)
            connector = await session.get(ConnectorRecord, revision.connector_id) if revision is not None else None
        if revision is None or connector is None or not connector.enabled:
            await self._fail(setup_id, "connector_disabled")
            raise ConnectorError("Connector is disabled or unavailable.", code="connector_disabled")
        provider_connection = ConnectorProviderConnection(
            provider_state_version=result.provider_state_version,
            provider_state=result.provider_state,
            secrets=result.secrets,
        )
        try:
            await self._providers.validate_connection(
                revision.provider_key,
                provider_config_version=revision.provider_config_version,
                config=revision.config,
                connection=provider_connection,
            )
            provider_state = bounded_json_object(dict(result.provider_state), field_name="provider_state")
        except Exception:
            await self._fail(setup_id, "connection_incompatible")
            raise ConnectorError(
                "Provider returned an incompatible Connection.", code="connection_incompatible"
            ) from None
        now = datetime.now(UTC)
        connection_record = ConnectionRecord(
            id=new_object_id(CONNECTION_ID_PREFIX),
            organization_id=setup.organization_id,
            workspace_id=setup.workspace_id,
            connector_id=revision.connector_id,
            principal_type=setup.principal_type,
            principal_id=setup.principal_id,
            name=setup.name,
            provider_key=setup.provider_key,
            provider_state_version=result.provider_state_version,
            provider_state=provider_state,
            account_external_id=result.account.external_id,
            account_display_name=result.account.display_name,
            status="active",
            expires_at=result.expires_at,
            lifecycle_operation_id=None,
            cleanup_pending=False,
            version=1,
            created_by_type=setup.initiated_by_type,
            created_by_id=setup.initiated_by_id,
            created_at=now,
            updated_at=now,
        )
        actor = PrincipalRef(
            principal_type=setup.initiated_by_type,  # type: ignore[arg-type]
            principal_id=setup.initiated_by_id,
        )
        async with transaction(self._sessions) as session:
            current = await _get_setup(session, setup_id, for_update=True)
            if current.status == "completed":
                if current.connection_id is None:
                    raise ConnectorError("Completed setup has no Connection.", code="connection_incompatible")
                existing_connection = await session.get(ConnectionRecord, current.connection_id)
                if existing_connection is None:
                    raise ConnectorError("Completed setup Connection was not found.", code="not_found")
                return _receipt(current, connection=_connection(existing_connection))
            if current.status not in {"starting", "completing"}:
                raise ConnectorError("Connection setup cannot be completed.", code="connection_incompatible")
            if current.target_connection_id is not None:
                existing = await session.scalar(
                    select(ConnectionRecord)
                    .where(
                        ConnectionRecord.id == current.target_connection_id,
                        ConnectionRecord.organization_id == current.organization_id,
                        ConnectionRecord.workspace_id == current.workspace_id,
                        ConnectionRecord.connector_id == revision.connector_id,
                        ConnectionRecord.provider_key == revision.provider_key,
                        ConnectionRecord.status == "reauthorization_required",
                    )
                    .with_for_update()
                )
                if existing is None:
                    raise ConnectorError(
                        "Connection can no longer be reauthorized.",
                        code="connection_incompatible",
                    )
                if existing.principal_type != current.principal_type or existing.principal_id != current.principal_id:
                    raise ConnectorError("Connection ownership changed.", code="connection_incompatible")
                existing.provider_state_version = result.provider_state_version
                existing.provider_state = provider_state
                existing.account_external_id = result.account.external_id
                existing.account_display_name = result.account.display_name
                existing.expires_at = result.expires_at
                existing.status = "active"
                existing.version += 1
                existing.updated_at = now
                connection_record = existing
            else:
                session.add(connection_record)
                await session.flush()
            await self._secrets.replace_connection_secrets(
                session,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                connection_id=connection_record.id,
                secrets=result.secrets,
                actor=actor,
            )
            current.status = "completed"
            current.status_reason = None
            current.connection_id = connection_record.id
            current.continuation_ciphertext = None
            current.next_action = None
            current.updated_at = now
        return _receipt(current, connection=_connection(connection_record))

    async def _load_reauthorization_target(
        self,
        request: StartConnectionSetup,
        revision: ConnectorRevisionRecord,
    ) -> ConnectionRecord | None:
        if request.connection_id is None:
            return None
        async with short_session(self._sessions) as session:
            connection = await session.scalar(
                select(ConnectionRecord).where(
                    ConnectionRecord.id == request.connection_id,
                    ConnectionRecord.organization_id == request.organization_id,
                    ConnectionRecord.workspace_id == request.workspace_id,
                    ConnectionRecord.connector_id == revision.connector_id,
                    ConnectionRecord.provider_key == revision.provider_key,
                    ConnectionRecord.status == "reauthorization_required",
                )
            )
        if connection is None:
            raise ConnectorError("Connection cannot be reauthorized.", code="connection_incompatible")
        expected_type = request.principal_ref.principal_type if request.principal_ref is not None else None
        expected_id = request.principal_ref.principal_id if request.principal_ref is not None else None
        if (
            connection.principal_type != expected_type
            or connection.principal_id != expected_id
            or connection.name != request.name
        ):
            raise ConnectorError("Connection reauthorization cannot change identity.", code="connection_incompatible")
        return connection

    async def _completed_receipt(self, setup: ConnectionSetupRecord) -> ConnectionSetupReceipt:
        if setup.connection_id is None:
            raise ConnectorError("Completed setup has no Connection.", code="connection_incompatible")
        async with short_session(self._sessions) as session:
            connection = await session.get(ConnectionRecord, setup.connection_id)
        if connection is None:
            raise ConnectorError("Completed setup Connection was not found.", code="not_found")
        return _receipt(setup, connection=_connection(connection))

    async def _fail(self, setup_id: str, reason: str) -> None:
        async with transaction(self._sessions) as session:
            record = await _get_setup(session, setup_id, for_update=True)
            if record.status != "completed":
                record.status = "failed"
                record.status_reason = reason
                record.continuation_ciphertext = None
                record.next_action = None
                record.updated_at = datetime.now(UTC)

    async def _load_target(
        self,
        organization_id: str,
        workspace_id: str,
        revision_id: str,
    ) -> tuple[ConnectorRevisionRecord, ConnectorRecord]:
        async with short_session(self._sessions) as session:
            revision = await session.scalar(
                select(ConnectorRevisionRecord).where(
                    ConnectorRevisionRecord.id == revision_id,
                    ConnectorRevisionRecord.organization_id == organization_id,
                    ConnectorRevisionRecord.workspace_id == workspace_id,
                )
            )
            connector = (
                await session.scalar(
                    select(ConnectorRecord).where(
                        ConnectorRecord.id == revision.connector_id,
                        ConnectorRecord.organization_id == organization_id,
                        ConnectorRecord.workspace_id == workspace_id,
                    )
                )
                if revision is not None
                else None
            )
        if revision is None or connector is None:
            raise ConnectorError("Connector revision was not found.", code="not_found")
        if not connector.enabled:
            raise ConnectorError("Connector is disabled.", code="connector_disabled")
        return revision, connector

    async def _find_operation(self, operation_id: str) -> ConnectionSetupRecord | None:
        async with short_session(self._sessions) as session:
            return await session.scalar(
                select(ConnectionSetupRecord).where(ConnectionSetupRecord.operation_id == operation_id)
            )


async def _get_setup(
    session: AsyncSession,
    setup_id: str,
    *,
    for_update: bool = False,
) -> ConnectionSetupRecord:
    statement: Select[tuple[ConnectionSetupRecord]] = select(ConnectionSetupRecord).where(
        ConnectionSetupRecord.id == setup_id
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise ConnectorError("Connection setup was not found.", code="not_found")
    return record


def _require_setup_eligible(
    record: ConnectionSetupRecord,
    *,
    actor: PrincipalRef | None,
    provider_key: str | None,
) -> None:
    _require_setup_identity(record, actor=actor, provider_key=provider_key)
    if _utc(record.expires_at) <= datetime.now(UTC):
        raise ConnectorError("Connection setup has expired.", code="connection_incompatible")
    if record.status not in {"pending", "completing"}:
        raise ConnectorError("Connection setup cannot be completed.", code="connection_incompatible")


def _require_setup_identity(
    record: ConnectionSetupRecord,
    *,
    actor: PrincipalRef | None,
    provider_key: str | None,
) -> None:
    if provider_key is not None and record.provider_key != provider_key:
        raise ConnectorError("Connection setup was not found.", code="not_found")
    if actor is not None and (
        actor.principal_type != record.initiated_by_type or actor.principal_id != record.initiated_by_id
    ):
        raise ConnectorError("Connection setup was not found.", code="not_found")


def _require_operation_replay(
    record: ConnectionSetupRecord,
    *,
    request: StartConnectionSetup,
    request_digest: str,
) -> None:
    if record.initiated_by_type != request.actor.principal_type or record.initiated_by_id != request.actor.principal_id:
        raise ConnectorError("Connection setup was not found.", code="not_found")
    if record.request_digest != request_digest:
        raise ConnectorError("Idempotency key was reused with different input.", code="version_conflict")


def _receipt(
    record: ConnectionSetupRecord,
    *,
    connection: Connection | None,
) -> ConnectionSetupReceipt:
    return ConnectionSetupReceipt(
        setup_id=record.id,
        provider_key=record.provider_key,
        expires_at=_utc(record.expires_at),
        completed=record.status == "completed",
        next_action=record.next_action,
        connection=connection,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
