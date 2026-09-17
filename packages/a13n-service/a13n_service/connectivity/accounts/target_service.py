"""Manage one exact provider object using Account defaults and bounded overrides."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.management import fingerprint, record_command
from a13n_service.connectivity.native_management import (
    audit,
    authorize,
    idempotency_key_digest,
    replay_command,
    require_adapter,
    require_limit,
    require_version,
)
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .models import AccountRecord
from .queries import require_account
from .reception import Reception
from .target_models import AccountTargetRecord
from .targets import AccountTarget, ReplaceTargetRequest, TargetCollection, TargetConfig
from .validation import validate_batching, validate_override, validate_reception


class AccountTargetService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[IngressAdapter],
        *,
        batch_max_events: int,
        batch_max_wait_seconds: float,
        clock: Clock = utc_now,
        target_deleted: Callable[[AsyncSession, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._batch_max_events = batch_max_events
        self._batch_max_wait_ms = round(batch_max_wait_seconds * 1000)
        self._clock = clock
        self._target_deleted = target_deleted

    async def create(
        self, *, actor: AuthenticatedActor, account_id: str, idempotency_key: str, request: TargetConfig
    ) -> AccountTarget:
        key = idempotency_key_digest(idempotency_key)
        digest = fingerprint(request)
        try:
            async with transaction(self._sessions) as session:
                account = await require_account(session, account_id, lock=True)
                await authorize(session, actor, account.workspace_id, WorkspaceAction.account_target_manage)
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=account.workspace_id,
                    operation="account_target.create",
                    scope_id=account_id,
                    idempotency_key_digest=key,
                    fingerprint=digest,
                    now=self._clock(),
                )
                if replay is not None:
                    await require_target(session, account_id, replay.resource_id)
                    return replay.restore(AccountTarget)
                candidate = await self._validate(session, actor, account, request)
                now = self._clock()
                record = AccountTargetRecord(
                    id=new_object_id("tgt"),
                    account_id=account_id,
                    organization_id=account.organization_id,
                    workspace_id=account.workspace_id,
                    version=1,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                _apply(record, candidate)
                session.add(record)
                record_command(
                    session,
                    actor=actor,
                    organization_id=account.organization_id,
                    workspace_id=account.workspace_id,
                    operation="account_target.create",
                    scope_id=account_id,
                    idempotency_key_digest=key,
                    fingerprint=digest,
                    resource_type="account_target",
                    resource_id=record.id,
                    result_version=1,
                    now=now,
                    resource=record.to_resource(),
                )
                session.add(
                    audit(actor, account.organization_id, account.workspace_id, "account_target.create", record.id, now)
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise NativeError(
                "target_conflict", "This provider object is already configured.", category=ErrorCategory.conflict
            ) from error

    async def replace(
        self, *, actor: AuthenticatedActor, account_id: str, target_id: str, request: ReplaceTargetRequest
    ) -> AccountTarget:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id, lock=True)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.account_target_manage)
            record = await require_target(session, account_id, target_id, lock=True)
            require_version(record.version, request.expected_version)
            candidate = await self._validate(
                session,
                actor,
                account,
                TargetConfig.model_validate(request.model_dump(exclude={"expected_version"}, exclude_unset=True)),
            )
            if candidate.target_kind != record.target_kind or candidate.external_target_id != record.external_target_id:
                raise NativeError(
                    "immutable_target_identity",
                    "Create another target to select a different provider object.",
                    category=ErrorCategory.conflict,
                )
            _apply(record, candidate)
            record.version += 1
            record.updated_at = self._clock()
            session.add(
                audit(
                    actor,
                    account.organization_id,
                    account.workspace_id,
                    "account_target.update",
                    record.id,
                    record.updated_at,
                )
            )
            await session.flush()
            return record.to_resource()

    async def get(self, *, actor: AuthenticatedActor, account_id: str, target_id: str) -> AccountTarget:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.account_target_read)
            return (await require_target(session, account_id, target_id)).to_resource()

    async def list(
        self, *, actor: AuthenticatedActor, account_id: str, limit: int, cursor: str | None
    ) -> TargetCollection:
        require_limit(limit)
        scope = {"account_id": account_id, "actor": actor.principal.model_dump(mode="json")}
        try:
            position = decode_cursor(cursor, scope=scope, id_prefix="tgt") if cursor else None
        except CursorError as error:
            raise NativeError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.account_target_read)
            query = select(AccountTargetRecord).where(AccountTargetRecord.account_id == account_id)
            if position is not None:
                query = query.where(
                    or_(
                        AccountTargetRecord.updated_at < position[0],
                        and_(AccountTargetRecord.updated_at == position[0], AccountTargetRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(AccountTargetRecord.updated_at.desc(), AccountTargetRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = (
                encode_cursor(updated_at=page[-1].updated_at, object_id=page[-1].id, scope=scope)
                if len(records) > limit
                else None
            )
            return TargetCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def delete(
        self, *, actor: AuthenticatedActor, account_id: str, target_id: str, expected_version: int
    ) -> None:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id, lock=True)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.account_target_manage)
            record = await require_target(session, account_id, target_id, lock=True)
            require_version(record.version, expected_version)
            if record.target_kind == "conversation":
                if self._target_deleted is not None:
                    await self._target_deleted(session, account_id, record.external_target_id)
            session.add(
                audit(
                    actor,
                    account.organization_id,
                    account.workspace_id,
                    "account_target.delete",
                    record.id,
                    self._clock(),
                )
            )
            await session.delete(record)

    async def _validate(
        self, session: AsyncSession, actor: AuthenticatedActor, account: AccountRecord, request: TargetConfig
    ) -> TargetConfig:
        adapter = require_adapter(self._adapters, account.provider_key, account.provider_config_version)
        try:
            identifier = adapter.validate_target(request.target_kind, request.external_target_id)
            policy = (
                adapter.validate_reception_policy(
                    request.provider_policy, config_version=account.provider_config_version
                )
                if request.provider_policy is not None
                else None
            )
        except ValueError as error:
            raise NativeError(
                "invalid_target", "Provider target configuration is invalid.", category=ErrorCategory.invalid_request
            ) from error
        agent_id = request.agent_id or account.default_agent_id
        execution_actor = await validate_reception(
            session,
            actor,
            account.workspace_id,
            Reception(default_agent_id=agent_id, execution_service_account_id=account.execution_service_account_id),
        )
        for principal in (actor,) if execution_actor is None else (actor, execution_actor):
            await validate_override(
                session,
                actor=principal,
                organization_id=account.organization_id,
                workspace_id=account.workspace_id,
                override=request.config_override,
            )
        validate_batching(
            request.input_batching, max_events=self._batch_max_events, max_interval_ms=self._batch_max_wait_ms
        )
        return request.model_copy(update={"external_target_id": identifier, "provider_policy": policy})


async def require_target(
    session: AsyncSession, account_id: str, target_id: str, *, lock: bool = False
) -> AccountTargetRecord:
    query = select(AccountTargetRecord).where(
        AccountTargetRecord.id == target_id, AccountTargetRecord.account_id == account_id
    )
    record = await session.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise NativeError("resource_not_found", "The requested target was not found.", category=ErrorCategory.not_found)
    return record


def _apply(record: AccountTargetRecord, value: TargetConfig) -> None:
    record.target_kind = value.target_kind
    record.external_target_id = value.external_target_id
    record.agent_id = value.agent_id
    record.config_override_json = (
        value.config_override.model_dump(mode="json", exclude_unset=True) if value.config_override is not None else None
    )
    record.input_batching_json = value.input_batching.model_dump(mode="json") if value.input_batching else None
    record.provider_policy_json = value.provider_policy
    record.receive_enabled = value.receive_enabled
