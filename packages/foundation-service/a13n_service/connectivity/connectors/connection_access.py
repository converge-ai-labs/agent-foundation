"""ConnectorConnection authorization and state invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connectors.contracts import (
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
)
from a13n_service.connectivity.management import (
    idempotency_key_digest,
    replay_command,
)
from a13n_service.durable_operations.idempotency import IdempotencyConflict, InvalidIdempotencyKey
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import (
    WorkspaceAction,
)

from .domain import ConnectorConnection
from .errors import ConnectorError
from .management import authorize, map_management_value_error, require_connection
from .models import ConnectorConnectionRecord, ConnectorSetupAttemptRecord


async def authorize_connection(
    session: AsyncSession,
    actor: AuthenticatedActor,
    connection: ConnectorConnectionRecord,
    *,
    mode: Literal["read", "manage"],
) -> None:
    action = (
        WorkspaceAction.connector_connection_read if mode == "read" else WorkspaceAction.connector_connection_manage
    )
    await authorize(session, actor, connection.workspace_id, action)


async def connection_resource(session: AsyncSession, connection_id: str) -> ConnectorConnection:
    record = await require_connection(session, connection_id)
    return record.to_resource()


def verify_inspection(
    attempt: ConnectorSetupAttemptRecord,
    connection: ConnectorConnectionRecord,
    inspection: ConnectionInspection,
) -> None:
    if (
        inspection.external_ref != attempt.external_ref
        or inspection.external_ref != connection.external_ref
        or inspection.connector_key != attempt.connector_key
        or inspection.connector_key != connection.connector_key
        or inspection.external_user_correlation != attempt.external_user_correlation
    ):
        raise ConnectorError(
            "connection_substitution",
            "ConnectorProvider returned another external account.",
            category=ErrorCategory.conflict,
        )


def apply_inspection(
    connection: ConnectorConnectionRecord,
    inspection: ConnectionInspection,
    *,
    now: datetime,
) -> None:
    connection.status = inspection.status.value
    connection.status_reason = inspection.status_reason.value if inspection.status_reason is not None else None
    connection.safe_metadata_json = inspection.safe_metadata
    connection.version += 1
    connection.updated_at = now


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise ConnectorError("version_conflict", "Resource version has changed.", category=ErrorCategory.conflict)


def idempotency_digest(value: str) -> str:
    try:
        return idempotency_key_digest(value)
    except InvalidIdempotencyKey as error:
        raise map_management_value_error(error) from error


async def replay_connection_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    connection: ConnectorConnectionRecord,
    operation: str,
    key_digest: str,
    request_fingerprint: str,
    now: datetime,
):
    try:
        return await replay_command(
            session,
            actor=actor,
            workspace_id=connection.workspace_id,
            operation=operation,
            scope_id=connection.id,
            idempotency_key_digest=key_digest,
            fingerprint=request_fingerprint,
            now=now,
        )
    except IdempotencyConflict as error:
        raise map_management_value_error(error) from error


def external_error(error: ConnectorProviderError) -> ConnectorError:
    if error.retryable or error.outcome_unknown:
        return ConnectorError(
            "connector_unavailable", "ConnectorProvider is unavailable.", category=ErrorCategory.unavailable
        )
    return ConnectorError(
        "connector_rejected", "ConnectorProvider rejected the operation.", category=ErrorCategory.conflict
    )


async def connection_binding(session: AsyncSession, connection: ConnectorConnectionRecord) -> ConnectionBinding:
    attempt = await session.scalar(
        select(ConnectorSetupAttemptRecord).where(
            ConnectorSetupAttemptRecord.connector_connection_id == connection.id,
            ConnectorSetupAttemptRecord.generation == connection.setup_generation,
            ConnectorSetupAttemptRecord.external_ref == connection.external_ref,
        )
    )
    if attempt is None or connection.external_ref is None:
        raise ConnectorError(
            "setup_unavailable", "Verified connection binding is unavailable.", category=ErrorCategory.conflict
        )
    return ConnectionBinding(
        external_ref=connection.external_ref,
        connector_key=connection.connector_key,
        external_user_correlation=attempt.external_user_correlation,
    )
