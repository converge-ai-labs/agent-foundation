"""Shared connection identity, authorization, and public projection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.temporal import assume_utc

from .models import ConnectionRecord


class ConnectionError(ApplicationError):
    pass


async def authorize(
    session: AsyncSession, actor: AuthenticatedActor, workspace_id: str, *, mode: Literal["read", "manage"]
):
    try:
        return await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.connection_read if mode == "read" else WorkspaceAction.connection_manage,
        )
    except AuthorizationError as error:
        raise ConnectionError(
            "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
        ) from error


async def require_connection(
    session: AsyncSession, connection_id: str, *, lock: bool = False, include_deleted: bool = False
) -> ConnectionRecord:
    query = select(ConnectionRecord).where(ConnectionRecord.id == connection_id)
    if not include_deleted:
        query = query.where(ConnectionRecord.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ConnectionError(
            "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
        )
    return record


def project(record: ConnectionRecord) -> Connection:
    source = (
        {"kind": "connector", "provider_id": record.connector_provider_id, "connector_key": record.connector_key}
        if record.kind == "connector"
        else {
            "kind": "mcp",
            "endpoint_url": record.endpoint_url,
            "auth_mode": record.auth_mode,
            "static_header_names": record.static_header_names_json,
        }
    )
    return Connection.model_validate(
        dict(
            id=record.id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            name=record.name,
            source=source,
            status=record.status,
            status_reason=record.status_reason,
            version=record.version,
            authorization_generation=record.authorization_generation,
            credential_configured=record.external_ref is not None
            if record.kind == "connector"
            else record.ciphertext is not None,
            safe_metadata=record.safe_metadata_json,
            last_check=record.last_check_json,
            created_by={"principal_type": record.created_by_type, "principal_id": record.created_by_id},
            created_at=assume_utc(record.created_at),
            updated_at=assume_utc(record.updated_at),
        )
    )


def require_version(record: ConnectionRecord, expected: int) -> None:
    if record.version != expected:
        raise ConnectionError("version_conflict", "Connection changed concurrently.", category=ErrorCategory.conflict)


def audit(
    actor: AuthenticatedActor, connection: ConnectionRecord, *, action: str, now: datetime
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=connection.organization_id,
        workspace_id=connection.workspace_id,
        action=action,
        resource_type="connection",
        resource_id=connection.id,
        outcome="success",
        occurred_at=now,
        details=None,
    )
