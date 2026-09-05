"""Shared ConnectorProvider persistence, IAM, credential, and audit helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.connectors.registry import ConnectorProviderImplementation, ConnectorProviderRegistry
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import ConnectivityManagementValueError
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.iam.resource_scope import ResourceScope, actor_scope, authorize_resource, authorize_scope
from a13n_service.ids import new_object_id

from .contracts import ConnectorProviderRuntime
from .errors import ConnectorError
from .models import ConnectorConnectionRecord, ConnectorProviderRecord

_JSON_OBJECT = TypeAdapter(JsonObject)


def require_implementation(registry: ConnectorProviderRegistry, provider_type: str) -> ConnectorProviderImplementation:
    try:
        return registry.require(provider_type)
    except ValueError as error:
        raise ConnectorError(
            "unsupported_connector_provider_type", "Connector Provider type is not registered.", status_code=400
        ) from error


def configure_provider(
    registry: ConnectorProviderRegistry, record: ConnectorProviderRecord, credentials: JsonObject
) -> ConnectorProviderRuntime:
    implementation = require_implementation(registry, record.type)
    try:
        return implementation.configure(record.configuration_json, credentials)
    except ValueError as error:
        raise ConnectorError(
            "invalid_connector_provider_configuration", "Connector Provider configuration is invalid.", status_code=409
        ) from error


async def authorize(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    action: WorkspaceAction,
):
    try:
        return await authorize_scope(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ConnectorError(
            "resource_not_found",
            "The requested resource was not found.",
            status_code=404,
        ) from error


async def connector_actor_scope(session: AsyncSession, actor: AuthenticatedActor) -> ResourceScope:
    try:
        return await actor_scope(session, actor)
    except AuthorizationError as error:
        raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404) from error


async def require_connector_provider(
    session: AsyncSession, connector_provider_id: str, *, scope: ResourceScope, lock: bool = False
) -> ConnectorProviderRecord:
    query = select(ConnectorProviderRecord).where(
        ConnectorProviderRecord.id == connector_provider_id,
        scope.accessible(ConnectorProviderRecord.organization_id, ConnectorProviderRecord.workspace_id),
    )
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


async def require_connection(
    session: AsyncSession,
    connection_id: str,
    *,
    lock: bool = False,
    include_deleted: bool = False,
) -> ConnectorConnectionRecord:
    query = select(ConnectorConnectionRecord).where(ConnectorConnectionRecord.id == connection_id)
    if not include_deleted:
        query = query.where(ConnectorConnectionRecord.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


def decode_credentials(value: str) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(json.loads(value))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
        raise ConnectorError(
            "credential_unavailable", "ConnectorProvider credentials are unavailable.", status_code=503
        ) from error


def audit(
    actor: AuthenticatedActor,
    *,
    organization_id: str,
    workspace_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
        occurred_at=now.astimezone(UTC),
        details=None,
    )


def map_management_value_error(error: ConnectivityManagementValueError) -> ConnectorError:
    if str(error) == "idempotency_conflict":
        return ConnectorError(
            "idempotency_conflict",
            "Idempotency key was used for another request.",
            status_code=409,
        )
    return ConnectorError("invalid_request", "Idempotency-Key is invalid.", status_code=400)


def require_active_provider(record: ConnectorProviderRecord) -> None:
    if record.status != "active":
        raise ConnectorError("connector_provider_disabled", "Connector Provider is disabled.", status_code=409)


async def authorize_provider(
    session: AsyncSession, actor: AuthenticatedActor, record: ConnectorProviderRecord, *, manage: bool = False
) -> None:
    try:
        await authorize_resource(
            session,
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            action=WorkspaceAction.connector_provider_manage if manage else WorkspaceAction.connector_provider_read,
            manage=manage,
        )
    except AuthorizationError as error:
        raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404) from error
