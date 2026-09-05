"""MCPConnection persistence, IAM, audit, and Secret helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.management import ConnectivityManagementValueError
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id

from .errors import MCPConnectionError
from .models import MCPConnectionRecord


async def authorize_workspace_action(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise not_found() from error


async def authorize_connection(
    session: AsyncSession,
    actor: AuthenticatedActor,
    connection: MCPConnectionRecord,
    *,
    mode: Literal["read", "manage"],
) -> None:
    action = WorkspaceAction.mcp_connection_read if mode == "read" else WorkspaceAction.mcp_connection_manage
    await authorize_workspace_action(session, actor, connection.workspace_id, action)


async def require_connection(
    session: AsyncSession,
    connection_id: str,
    *,
    lock: bool = False,
    include_deleted: bool = False,
) -> MCPConnectionRecord:
    query = select(MCPConnectionRecord).where(MCPConnectionRecord.id == connection_id)
    if not include_deleted:
        query = query.where(MCPConnectionRecord.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise not_found()
    return record


def audit(
    actor: AuthenticatedActor,
    connection: MCPConnectionRecord,
    *,
    action: str,
    now: datetime,
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=connection.organization_id,
        workspace_id=connection.workspace_id,
        action=action,
        resource_type="mcp_connection",
        resource_id=connection.id,
        outcome="success",
        occurred_at=now,
        details=None,
    )


def map_management_error(error: ConnectivityManagementValueError) -> MCPConnectionError:
    if str(error) == "idempotency_conflict":
        return MCPConnectionError(
            "idempotency_conflict",
            "Idempotency key was used for another request.",
            status_code=409,
        )
    return MCPConnectionError("invalid_request", "Idempotency-Key is invalid.", status_code=400)


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise MCPConnectionError("version_conflict", "MCPConnection changed concurrently.", status_code=409)


def invalidate_refresh_claim(connection: MCPConnectionRecord, *, now: datetime) -> None:
    """Fence in-flight credential refresh before an authorization-affecting change."""

    connection.refresh_claim_generation += 1
    connection.refresh_claim_owner = None
    connection.refresh_claim_expires_at = None


def not_found() -> MCPConnectionError:
    return MCPConnectionError("resource_not_found", "The requested resource was not found.", status_code=404)
