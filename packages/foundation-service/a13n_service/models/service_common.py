"""Shared application-layer helpers for Model Management."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.ids import new_object_id
from a13n_service.public_errors import PublicError


class ModelError(PublicError):
    """Safe stable error raised by the Model Management application layer."""


async def authorize_models(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    action: WorkspaceAction,
):
    try:
        return await authorize_scope(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ModelError(
            "resource_not_found" if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            status_code=404 if error.concealed else 403,
        ) from error


def require_etag(resource_id: str, updated_at: datetime, if_match: str) -> None:
    current = resource_etag(resource_id, updated_at)
    if not etag_matches(if_match, current):
        raise ModelError(
            "precondition_failed",
            "The resource changed after it was read.",
            status_code=412,
            details={"current_etag": current},
        )


def audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str | None,
    resource_type: str,
    resource_id: str | None,
    action: str,
    now: datetime,
    outcome: str = "success",
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        occurred_at=now,
        details=None,
    )


def is_unique_conflict(error: IntegrityError, *, constraint: str, sqlite_columns: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if diagnostic is not None:
        return diagnostic.sqlstate == "23505" and diagnostic.constraint_name == constraint
    return (
        getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(error.orig) == f"UNIQUE constraint failed: {sqlite_columns}"
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
