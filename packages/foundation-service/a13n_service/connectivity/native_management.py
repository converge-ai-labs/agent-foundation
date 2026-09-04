"""Shared persistence and validation primitives for Account, Ingress, and Route management."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
)
from a13n_service.connectivity.management import (
    idempotency_key_digest as shared_idempotency_key_digest,
)
from a13n_service.connectivity.management import (
    replay_command as shared_replay_command,
)
from a13n_service.connectivity.models import ConnectivityCommandRecord
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id

from .errors import NativeError


def require_adapter(
    adapters: AdapterRegistry[IngressAdapter], provider_key: str, config_version: str
) -> IngressAdapter:
    try:
        return adapters.create(provider_key, config_version=config_version)
    except ValueError as error:
        raise NativeError(
            "unsupported_ingress_adapter", "Ingress adapter is not registered.", status_code=400
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
        raise NativeError("resource_not_found", "The requested resource was not found.", status_code=404) from error


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
) -> ConnectivityCommandRecord | None:
    try:
        return await shared_replay_command(
            session,
            actor=actor,
            workspace_id=workspace_id,
            operation=operation,
            scope_id=scope_id,
            idempotency_key_digest=idempotency_key_digest,
            fingerprint=fingerprint,
        )
    except ConnectivityManagementValueError as error:
        raise NativeError(
            "idempotency_conflict", "Idempotency key was used for another request.", status_code=409
        ) from error


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise NativeError("version_conflict", "Resource version has changed.", status_code=409)


def require_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise NativeError("invalid_request", "Collection limit must be between 1 and 100.", status_code=400)


def idempotency_key_digest(value: str) -> str:
    try:
        return shared_idempotency_key_digest(value)
    except ConnectivityManagementValueError as error:
        raise NativeError("invalid_request", "Idempotency-Key is invalid.", status_code=400) from error


def audit(
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type=action.split(".", 1)[0],
        resource_id=resource_id,
        outcome="success",
        occurred_at=now.astimezone(UTC),
        details=None,
    )
