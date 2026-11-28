"""Authorized subscription commands; external requests happen after configuration commits."""

import hashlib
from datetime import UTC, datetime

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue, TypeAdapter
from referencing.exceptions import Unresolvable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import advisory_lock, now, short_session, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools.discovery import unavailable
from a13n_service.resources.connections.access import ConnectorConnection, resolve_connection
from a13n_service.resources.connections.credentials import AccountSecret, reveal
from a13n_service.resources.connector_providers.events import open_events
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.accept import run_revision
from a13n_service.runs.connector_events.triggers.schemas import (
    ConnectorTrigger as Trigger,
)
from a13n_service.runs.connector_events.triggers.schemas import (
    EventThreadTarget as NewThread,
)
from a13n_service.runs.connector_events.triggers.schemas import (
    Target,
    TriggerCreate,
    TriggerPage,
    TriggerUpdate,
)
from a13n_service.runs.connector_events.triggers.state import UNOWNED, retryable
from a13n_service.runs.connector_events.triggers.tables import TriggerRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import canonical_json
from a13n_service.tenancy.access import principal_for, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, authorize, execution_authority

TARGET = TypeAdapter(Target)


def view(row: TriggerRow) -> Trigger:
    return Trigger.model_validate(row).model_copy(update={"retryable": retryable(row, datetime.now(UTC))})


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def schema_valid(schema: dict, value: JsonValue) -> bool:
    """Only bounded, local JSON Schema references; validation must never perform network I/O."""
    pending = [schema]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        if count > 10000:
            raise invalid("event_schema", "too_complex")
        if isinstance(item, dict):
            for key in ("$ref", "$dynamicRef"):
                ref = item.get(key)
                if ref is not None and (not isinstance(ref, str) or not ref.startswith("#")):
                    raise invalid("event_schema", "external_reference")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema).is_valid(value)
    except (SchemaError, Unresolvable, RecursionError):
        raise invalid("event_schema", "invalid_schema") from None


async def target_snapshot(
    session: AsyncSession, runtime: Runtime, actor: Principal, scope: WorkspaceScope, target: Target
) -> dict:
    if isinstance(target, NewThread):
        # Choosing a durable service-account identity is administrative delegation.
        authorize(actor, scope, "admin")
        principal = await principal_for(session, runtime.access, target.principal_id, confinement=scope)
        if principal.kind != "service_account":
            raise invalid("target.principal_id", "service_account_required")
        await workspace_scope(session, principal, scope.workspace_id, "run")
        revision, _ = await run_revision(
            session,
            runtime,
            principal,
            scope,
            target.agent_id,
            target.agent_revision_id,
            None,
            authority=execution_authority(principal, scope),
        )
        target = target.model_copy(update={"agent_revision_id": revision.revision_id})
    return target.model_dump(mode="json")


def account(runtime: Runtime, connection: ConnectorConnection) -> AccountSecret:
    if connection.status != "ready" or connection.credential is None:
        raise conflict("connection", connection.id, "not_authorized")
    return reveal(
        runtime.keys, connection.organization_id, connection.id, "credential", connection.credential, AccountSecret
    )


async def create(
    runtime: Runtime, actor: Principal, workspace_id: str, body: TriggerCreate, *, request_key: str
) -> tuple[Trigger, bool]:
    request_digest = digest(body.model_dump(mode="json"))
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        existing = await session.scalar(
            select(TriggerRow).where(
                TriggerRow.workspace_id == scope.workspace_id,
                TriggerRow.created_by_id == actor.id,
                TriggerRow.request_key == request_key,
            )
        )
        if existing:
            if existing.request_digest != request_digest:
                raise conflict("request", request_key, "idempotency_key_reused")
            return view(existing), False
        connection = await resolve_connection(session, actor, scope, body.connection_id, verb="run")
        if not isinstance(connection, ConnectorConnection):
            raise invalid("connection_id", "connector_connection_required")
        bound = account(runtime, connection)
    try:
        async with open_events(runtime, connection.provider) as adapter:
            definition = await adapter.event_type(connection.config.app, body.event_type)
    except Exception as error:
        raise unavailable(f"connector:{connection.provider.type}", error) from None
    if not schema_valid(definition.config_schema, body.parameters):
        raise invalid("parameters", "event_configuration_invalid")
    if definition.payload_schema is not None:
        schema_valid(definition.payload_schema, {})
    if len(canonical_json(body.parameters)) > 65536:
        raise invalid("parameters", "too_large")
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        await advisory_lock(session, "connector_triggers", scope.workspace_id)
        existing = await session.scalar(
            select(TriggerRow).where(
                TriggerRow.workspace_id == scope.workspace_id,
                TriggerRow.created_by_id == actor.id,
                TriggerRow.request_key == request_key,
            )
        )
        if existing:
            if existing.request_digest != request_digest:
                raise conflict("request", request_key, "idempotency_key_reused")
            return view(existing), False
        current = await resolve_connection(session, actor, scope, body.connection_id, verb="run")
        if not isinstance(current, ConnectorConnection) or current.version != connection.version:
            raise conflict("connection", body.connection_id, "connection_changed")
        provider = await session.get_one(ConnectorProviderRow, current.provider.id)
        if provider.event_secret is None or provider.event_operation is not None:
            raise conflict(provider.KIND, provider.id, "event_webhook_not_ready")
        target = await target_snapshot(session, runtime, actor, scope, body.target)
        config_digest = digest(body.parameters)
        duplicate = await session.scalar(
            select(TriggerRow.id)
            .where(
                TriggerRow.provider_id == provider.id,
                TriggerRow.account_id == bound.account_ref,
                TriggerRow.event_type == body.event_type,
                TriggerRow.config_digest == config_digest,
                TriggerRow.state != "retired",
            )
            .limit(1)
        )
        if duplicate:
            raise conflict("connector_trigger", duplicate, "subscription_already_bound")
        row = TriggerRow(
            id=new_object_id("ctrig"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=provider.id,
            connection_id=current.id,
            account_id=bound.account_ref,
            user_id=bound.correlation,
            name=body.name,
            event_type=definition.key,
            definition=definition.model_dump(mode="json"),
            parameters=body.parameters,
            config_digest=config_digest,
            target=target,
            initial_target=target,
            state="enabled",
            operation_id=new_object_id("ctop"),
            operation_kind="create",
            operation_due_at=await now(session),
            operation_attempts=0,
            request_key=request_key,
            request_digest=request_digest,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        audit_row(session, actor, row, "create")
        await session.flush()
        return view(row), True


async def get(runtime: Runtime, actor: Principal, workspace_id: str, trigger_id: str) -> Trigger:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return view(await find_row(session, actor, TriggerRow, scope, trigger_id, "read"))


async def list_triggers(
    runtime: Runtime, actor: Principal, workspace_id: str, *, connection_id: str | None, limit: int, cursor: str | None
) -> TriggerPage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(TriggerRow).where(TriggerRow.workspace_id == scope.workspace_id)
        if connection_id:
            query = query.where(TriggerRow.connection_id == connection_id)
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            TriggerRow.id,
            kind="connector_triggers",
            owner=cursors.query_owner(scope.workspace_id, connection_id or ""),
            cursor=cursor,
            limit=limit,
        )
        return TriggerPage(items=[view(row) for row in rows], next_cursor=next_cursor)


async def update(
    runtime: Runtime, actor: Principal, workspace_id: str, trigger_id: str, body: TriggerUpdate, *, if_match: str | None
) -> Trigger:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, TriggerRow, scope, trigger_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row.state == "retired" or row.operation_id is not None:
            raise conflict(row.KIND, row.id, "subscription_not_editable")
        if body.target is not None:
            row.target = await target_snapshot(session, runtime, actor, scope, body.target)
        if body.name is not None:
            row.name = body.name
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "update")
        await session.flush()
        return view(row)


async def reconcile(
    runtime: Runtime, actor: Principal, workspace_id: str, trigger_id: str, *, if_match: str | None
) -> Trigger:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, TriggerRow, scope, trigger_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if not retryable(row, await now(session)):
            raise conflict(row.KIND, row.id, row.failure or "operation_not_retryable")
        row.operation_id = new_object_id("ctop")
        row.operation_due_at, row.operation_attempts, row.failure = await now(session), 0, None
        audit_row(session, actor, row, "reconcile")
        await session.flush()
        return view(row)


async def transition(
    runtime: Runtime, actor: Principal, workspace_id: str, trigger_id: str, state: str, *, if_match: str | None
) -> Trigger:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, TriggerRow, scope, trigger_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row.operation_id is not None and state == "enabled":
            raise conflict(row.KIND, row.id, "operation_in_progress")
        if row.state == "retired" and state != "retired":
            raise conflict(row.KIND, row.id, "subscription_retired")
        if state == "enabled":
            await target_snapshot(session, runtime, actor, scope, TARGET.validate_python(row.target))
            connection = await resolve_connection(session, actor, scope, row.connection_id, verb="run")
            if (
                not isinstance(connection, ConnectorConnection)
                or account(runtime, connection).account_ref != row.account_id
            ):
                raise conflict(row.KIND, row.id, "connection_changed")
        if state != "enabled" and row.provisioned_at is None and row.provisioning_stopped_at is None:
            row.provisioning_stopped_at = await now(session)
        row.state = state
        if row.operation_id is None:
            row.operation_id, row.operation_kind = new_object_id("ctop"), state
            row.operation_due_at, row.operation_attempts, row.failure = await now(session), 0, None
        elif row.failure in UNOWNED and state == "retired":
            # An unowned or unconfirmed remote subscription is not ours to delete.
            row.operation_id, row.operation_kind, row.operation_due_at = None, None, None
        row.updated_by_id = actor.id
        audit_row(session, actor, row, state)
        await session.flush()
        return view(row)
