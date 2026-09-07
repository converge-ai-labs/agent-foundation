"""Shared persistence and validation primitives for native provider resources."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.composition import AdapterResolver
from a13n_service.connectivity.management import CommandReceipt
from a13n_service.connectivity.management import (
    replay_command as shared_replay_command,
)
from a13n_service.durable_operations.idempotency import (
    IdempotencyConflict,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
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


def require_adapter[AdapterT](adapters: AdapterResolver[AdapterT], provider_key: str, config_version: str) -> AdapterT:
    try:
        return adapters.create(provider_key, config_version=config_version)
    except ValueError as error:
        raise NativeError(
            "unsupported_ingress_adapter", "Ingress adapter is not registered.", category=ErrorCategory.invalid_request
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
        raise NativeError(
            "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
        ) from error


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
    now: datetime,
) -> CommandReceipt | None:
    try:
        return await shared_replay_command(
            session,
            actor=actor,
            workspace_id=workspace_id,
            operation=operation,
            scope_id=scope_id,
            idempotency_key_digest=idempotency_key_digest,
            fingerprint=fingerprint,
            now=now,
        )
    except IdempotencyConflict as error:
        raise NativeError(
            "idempotency_conflict", "Idempotency key was used for another request.", category=ErrorCategory.conflict
        ) from error


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise NativeError("version_conflict", "Resource version has changed.", category=ErrorCategory.conflict)


def require_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise NativeError(
            "invalid_request", "Collection limit must be between 1 and 100.", category=ErrorCategory.invalid_request
        )


def idempotency_key_digest(value: str) -> str:
    try:
        return digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise NativeError(
            "invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request
        ) from error


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
