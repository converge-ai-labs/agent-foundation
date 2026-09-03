"""Shared Connector persistence, IAM, credential, and audit helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import ConnectorAdapter
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.management import ConnectivityManagementValueError
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretOperation, SecretOwnerType, SecretUseContext

from .errors import ConnectorError
from .models import ConnectorConnectionRecord, ConnectorRecord

_JSON_OBJECT = TypeAdapter(JsonObject)


def require_adapter(
    adapters: AdapterRegistry[ConnectorAdapter], driver_key: str, config_version: str
) -> ConnectorAdapter:
    try:
        return adapters.create(driver_key, config_version=config_version)
    except ValueError as error:
        raise ConnectorError(
            "unsupported_connector_adapter",
            "Connector adapter is not registered.",
            status_code=400,
        ) from error


async def authorize(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ConnectorError(
            "resource_not_found",
            "The requested resource was not found.",
            status_code=404,
        ) from error


async def require_connector(session: AsyncSession, connector_id: str, *, lock: bool = False) -> ConnectorRecord:
    query = select(ConnectorRecord).where(ConnectorRecord.id == connector_id)
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


def secret_context(
    connector: ConnectorRecord,
    *,
    operation: SecretOperation,
    generation: int | None = None,
) -> SecretUseContext:
    return SecretUseContext(
        organization_id=connector.organization_id,
        workspace_id=connector.workspace_id,
        owner_type=SecretOwnerType.connector,
        owner_id=connector.id,
        key="api_key",
        operation=operation,
        credential_generation=generation or connector.credential_generation,
    )


def decode_credentials(value: str) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(json.loads(value))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
        raise ConnectorError(
            "credential_unavailable", "Connector credentials are unavailable.", status_code=503
        ) from error


def audit(
    actor: AuthenticatedActor,
    *,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now.astimezone(UTC),
        request_id=actor.request_id,
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
