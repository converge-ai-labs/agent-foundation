"""Transactional Application Account resource management."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.adapters import AccountAdapter, JsonObject
from a13n_service.connectivity.composition import AdapterResolver
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.management import canonical_json, clear_credentials, record_command
from a13n_service.connectivity.native_management import (
    audit,
    authorize,
    idempotency_key_digest,
    replay_command,
    require_adapter,
    require_limit,
    require_version,
)
from a13n_service.connectivity.transports.configuration import connection_key
from a13n_service.connectivity.transports.configuration import validate_credentials as validate_transport_credentials
from a13n_service.connectivity.transports.models import EventConnectionRecord
from a13n_service.digests import digest_request
from a13n_service.durable_operations.entity_keys import entity_key, find_by_key
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    WorkspaceAction,
)
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import (
    Account,
    AccountCollection,
    AccountProviderDefinitionCollection,
    AccountStatus,
    CreateAccountRequest,
    EventConnectionStatus,
    ReplaceAccountCredentialsRequest,
    UpdateAccountRequest,
)
from .models import AccountRecord
from .queries import require_account
from .reception import Reception
from .validation import validate_batching, validate_reception


class AccountService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterResolver[AccountAdapter],
        protector: SecretProtector,
        *,
        batch_max_events: int = 100,
        batch_max_wait_seconds: float = 300,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._clock = clock
        self._batch_max_events = batch_max_events
        self._batch_max_interval_ms = round(batch_max_wait_seconds * 1000)

    async def provider_types(
        self, *, actor: AuthenticatedActor, workspace_id: str
    ) -> AccountProviderDefinitionCollection:
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.application_account_read)
        return AccountProviderDefinitionCollection(
            items=tuple(
                require_adapter(self._adapters, key, version).describe_account(config_version=version)
                for key, version in self._adapters.versions()
            )
        )

    async def create_account(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: CreateAccountRequest,
    ) -> Account:
        key_digest = idempotency_key_digest(idempotency_key)
        account_id = new_object_id("acct")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
                replay = await find_by_key(
                    session,
                    AccountRecord,
                    entity_key(
                        actor,
                        operation="application_account.create",
                        scope_id=workspace_id,
                        key_digest=key_digest,
                        workspace_id=workspace_id,
                    ),
                )
                if replay is not None:
                    return replay.to_resource()
                adapter = require_adapter(self._adapters, request.provider_key, request.provider_config_version)
                config = _validate_config(adapter, request.provider_config, request.provider_config_version)
                credentials = _validate_credentials(adapter, request.credentials, request.provider_config_version)
                validate_transport_credentials(request.provider_key, config, credentials)
                reception = _parse_reception(request.model_dump(include=set(Reception.model_fields)))
                await validate_reception(session, actor, workspace_id, reception)
                validate_batching(
                    reception.input_batching,
                    max_events=self._batch_max_events,
                    max_interval_ms=self._batch_max_interval_ms,
                )
                policy = (
                    _validate_policy(adapter, reception.provider_policy, request.provider_config_version)
                    if reception.provider_policy is not None
                    else None
                )
                record = AccountRecord(
                    reception_scope=reception.reception_scope.value,
                    receive_enabled=reception.receive_enabled,
                    default_agent_id=reception.default_agent_id,
                    execution_service_account_id=reception.execution_service_account_id,
                    input_batching_json=reception.input_batching.model_dump() if reception.input_batching else None,
                    provider_policy_json=policy,
                    id=account_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    provider_key=request.provider_key,
                    provider_config_version=request.provider_config_version,
                    provider_config_json=config,
                    identity_digest=digest_request(
                        adapter.configuration_identity(config, config_version=request.provider_config_version)
                    ),
                    status=AccountStatus.active.value,
                    version=1,
                    credential_generation=0,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                record.request_key = entity_key(
                    actor,
                    operation="application_account.create",
                    scope_id=workspace_id,
                    key_digest=key_digest,
                    workspace_id=workspace_id,
                )
                record.replace_credential(canonical_json(credentials), self._protector)
                session.add(record)
                await session.flush()
                session.add(
                    audit(actor, workspace.organization_id, workspace_id, "application_account.create", account_id, now)
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            async with transaction(self._sessions) as session:
                await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
                replay = await find_by_key(
                    session,
                    AccountRecord,
                    entity_key(
                        actor,
                        operation="application_account.create",
                        scope_id=workspace_id,
                        key_digest=key_digest,
                        workspace_id=workspace_id,
                    ),
                )
                if replay is not None:
                    return replay.to_resource()
            raise NativeError(
                "account_conflict",
                "Application Account identity or name already exists.",
                category=ErrorCategory.conflict,
            ) from error
        except SecretProtectionError as error:
            raise NativeError(
                "credential_conflict", "Account credentials could not be stored.", category=ErrorCategory.conflict
            ) from error

    async def list_accounts(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> AccountCollection:
        require_limit(limit)
        scope = {"workspace_id": workspace_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="acct") if cursor is not None else None
        except CursorError as error:
            raise NativeError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize(session, actor, workspace_id, WorkspaceAction.application_account_read)
            query = select(AccountRecord).where(
                AccountRecord.organization_id == workspace.organization_id,
                AccountRecord.workspace_id == workspace_id,
                AccountRecord.deleted_at.is_(None),
            )
            if position is not None:
                query = query.where(
                    or_(
                        AccountRecord.updated_at < position[0],
                        and_(AccountRecord.updated_at == position[0], AccountRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            items = tuple(record.to_resource() for record in page)
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return AccountCollection(items=items, next_cursor=next_cursor)

    async def get_account(self, *, actor: AuthenticatedActor, account_id: str) -> Account:
        async with transaction(self._sessions) as session:
            record = await require_account(session, account_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.application_account_read)
            return record.to_resource()

    async def event_connection(self, *, actor: AuthenticatedActor, account_id: str) -> EventConnectionStatus:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.application_account_read)
            websocket = account.provider_config_json.get("event_transport") == "websocket"
            transport = "websocket" if websocket else "http"
            if account.status != "active":
                return EventConnectionStatus(transport=transport, state="disabled")
            if not websocket:
                return EventConnectionStatus(transport="http", state="http")
            record = await session.get(
                EventConnectionRecord, connection_key(account.provider_key, account.provider_config_json)
            )
            if record is None or record.account_versions.get(account.id) != account.version:
                return EventConnectionStatus(transport="websocket", state="connecting")
            state = record.state if assume_utc(record.lease_expires_at) > self._clock() else "disconnected"
            return EventConnectionStatus.model_validate(
                {
                    "transport": "websocket",
                    "state": state,
                    "observed_at": record.observed_at,
                    "last_event_at": record.last_event_at,
                    "error_code": record.error_code,
                }
            )

    async def update_account(
        self,
        *,
        actor: AuthenticatedActor,
        account_id: str,
        request: UpdateAccountRequest,
    ) -> Account:
        try:
            async with transaction(self._sessions) as session:
                return await self.update_in_session(session, actor=actor, account_id=account_id, request=request)
        except IntegrityError as error:
            raise NativeError(
                "account_conflict", "Application Account name already exists.", category=ErrorCategory.conflict
            ) from error

    async def update_in_session(
        self, session: AsyncSession, *, actor: AuthenticatedActor, account_id: str, request: UpdateAccountRequest
    ) -> Account:
        """Apply canonical account validation inside an already-owned short transaction."""
        record = await require_account(session, account_id, lock=True)
        await authorize(session, actor, record.workspace_id, WorkspaceAction.application_account_manage)
        require_version(record.version, request.expected_version)
        adapter = require_adapter(self._adapters, record.provider_key, record.provider_config_version)
        config = record.provider_config_json
        if request.provider_config is not None:
            config = _validate_config(adapter, request.provider_config, record.provider_config_version)
            if (
                digest_request(adapter.configuration_identity(config, config_version=record.provider_config_version))
                != record.identity_digest
            ):
                raise NativeError(
                    "immutable_account_identity",
                    "Application Account identity cannot be changed.",
                    category=ErrorCategory.conflict,
                )
        if request.provider_config is not None:
            validate_transport_credentials(
                record.provider_key, config, json.loads(record.credential_snapshot().decrypt(self._protector))
            )
        if request.name is not None:
            record.name = request.name
            record.normalized_name = request.name.casefold()
        reception = _parse_reception(
            {
                **record.to_resource().model_dump(include=set(Reception.model_fields)),
                **request.model_dump(include=set(Reception.model_fields), exclude_unset=True),
            }
        )
        await validate_reception(session, actor, record.workspace_id, reception)
        validate_batching(
            reception.input_batching,
            max_events=self._batch_max_events,
            max_interval_ms=self._batch_max_interval_ms,
        )
        record.reception_scope = reception.reception_scope.value
        record.receive_enabled = reception.receive_enabled
        record.default_agent_id = reception.default_agent_id
        record.execution_service_account_id = reception.execution_service_account_id
        record.input_batching_json = reception.input_batching.model_dump() if reception.input_batching else None
        record.provider_policy_json = (
            _validate_policy(adapter, reception.provider_policy, record.provider_config_version)
            if reception.provider_policy is not None
            else None
        )
        record.provider_config_json = config
        record.version += 1
        record.updated_at = self._clock()
        session.add(
            audit(
                actor,
                record.organization_id,
                record.workspace_id,
                "application_account.update",
                record.id,
                record.updated_at,
            )
        )
        await session.flush()
        return record.to_resource()

    async def replace_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        account_id: str,
        idempotency_key: str,
        request: ReplaceAccountCredentialsRequest,
    ) -> Account:
        key_digest = idempotency_key_digest(idempotency_key)
        async with transaction(self._sessions) as session:
            record = await require_account(session, account_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.application_account_manage)
            replay = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation="application_account.credentials",
                scope_id=account_id,
                idempotency_key_digest=key_digest,
                now=self._clock(),
            )
            if replay is not None:
                if replay.resource_id != record.id:
                    raise NativeError(
                        "idempotency_conflict",
                        "Idempotency key was used for another resource.",
                        category=ErrorCategory.conflict,
                    )
                return record.to_resource()
            require_version(record.version, request.expected_version)
            adapter = require_adapter(self._adapters, record.provider_key, record.provider_config_version)
            credentials = _validate_credentials(adapter, request.credentials, record.provider_config_version)
            validate_transport_credentials(record.provider_key, record.provider_config_json, credentials)
            record.replace_credential(canonical_json(credentials), self._protector)

            record.version += 1
            record.updated_at = self._clock()
            _record_account_command(
                session,
                actor,
                record,
                "application_account.credentials",
                key_digest,
                now=self._clock(),
            )
            session.add(
                audit(
                    actor,
                    record.organization_id,
                    record.workspace_id,
                    "application_account.credentials",
                    account_id,
                    record.updated_at,
                )
            )
            await session.flush()
            return record.to_resource()

    async def delete_account(self, *, actor: AuthenticatedActor, account_id: str, expected_version: int) -> None:
        async with transaction(self._sessions) as session:
            record = await require_account(session, account_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.application_account_manage)
            require_version(record.version, expected_version)
            record.status = AccountStatus.disabled.value
            record.clear_credential()
            now = self._clock()
            record.deleted_at = now
            record.updated_at = now
            record.version += 1
            session.add(
                audit(
                    actor,
                    record.organization_id,
                    record.workspace_id,
                    "application_account.delete",
                    record.id,
                    record.updated_at,
                )
            )
            await session.flush()

    async def set_status(
        self,
        *,
        actor: AuthenticatedActor,
        account_id: str,
        status: AccountStatus,
        expected_version: int,
        idempotency_key: str,
    ) -> Account:
        key_digest = idempotency_key_digest(idempotency_key)
        operation = "application_account.enable" if status is AccountStatus.active else "application_account.disable"
        async with transaction(self._sessions) as session:
            record = await require_account(session, account_id, lock=True)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.application_account_manage)
            replay = await replay_command(
                session,
                actor=actor,
                workspace_id=record.workspace_id,
                operation=operation,
                scope_id=account_id,
                idempotency_key_digest=key_digest,
                now=self._clock(),
            )
            if replay is not None:
                return record.to_resource()
            require_version(record.version, expected_version)
            if record.status != status.value:
                record.status = status.value
                record.version += 1
                record.updated_at = self._clock()
            _record_account_command(session, actor, record, operation, key_digest, now=self._clock())
            session.add(
                audit(actor, record.organization_id, record.workspace_id, operation, account_id, record.updated_at)
            )
            await session.flush()
            return record.to_resource()


def _validate_config(adapter: AccountAdapter, value: object, version: str) -> JsonObject:
    try:
        return adapter.validate_config(value, config_version=version)
    except ValueError as error:
        raise NativeError(
            "invalid_provider_config",
            "Account provider configuration is invalid.",
            category=ErrorCategory.invalid_request,
        ) from error


def _validate_credentials(adapter: AccountAdapter, value: dict[str, SecretStr], version: str) -> JsonObject:
    try:
        return adapter.validate_credentials(clear_credentials(value), config_version=version)
    except ValueError as error:
        raise NativeError(
            "invalid_credentials", "Account credentials are invalid.", category=ErrorCategory.invalid_request
        ) from error


def _record_account_command(
    session: AsyncSession,
    actor: AuthenticatedActor,
    record: AccountRecord,
    operation: str,
    key_digest: str,
    *,
    now: datetime,
) -> None:
    record_command(
        session,
        actor=actor,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        operation=operation,
        scope_id=record.id,
        idempotency_key_digest=key_digest,
        resource_type="application_account",
        resource_id=record.id,
        now=now,
    )


def _parse_reception(value: object) -> Reception:
    try:
        return Reception.model_validate(value)
    except ValueError as error:
        raise NativeError(
            "invalid_reception", "Account reception configuration is invalid.", category=ErrorCategory.invalid_request
        ) from error


def _validate_policy(adapter: AccountAdapter, value: JsonObject, version: str) -> JsonObject:
    try:
        return adapter.validate_reception_policy(value, config_version=version)
    except ValueError as error:
        raise NativeError(
            "invalid_reception_policy", "Provider reception policy is invalid.", category=ErrorCategory.invalid_request
        ) from error
