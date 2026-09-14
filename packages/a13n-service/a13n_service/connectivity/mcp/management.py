"""Connection persistence, IAM, audit, and Secret helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import IdempotencyConflict, InvalidIdempotencyKey
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace

from ..connections.access import audit as audit
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
    action = WorkspaceAction.connection_read if mode == "read" else WorkspaceAction.connection_manage
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


def map_management_error(error: IdempotencyConflict | InvalidIdempotencyKey) -> MCPConnectionError:
    if isinstance(error, IdempotencyConflict):
        return MCPConnectionError(
            "idempotency_conflict",
            "Idempotency key was used for another request.",
            category=ErrorCategory.conflict,
        )
    return MCPConnectionError("invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request)


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise MCPConnectionError(
            "version_conflict", "Connection changed concurrently.", category=ErrorCategory.conflict
        )


def invalidate_refresh_claim(connection: MCPConnectionRecord, *, now: datetime) -> None:
    """Fence in-flight credential refresh before an authorization-affecting change."""

    connection.refresh_claim_generation += 1
    connection.refresh_claim_owner = None
    connection.refresh_claim_expires_at = None


def not_found() -> MCPConnectionError:
    return MCPConnectionError(
        "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
    )
