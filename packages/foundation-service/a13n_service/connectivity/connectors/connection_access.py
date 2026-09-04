"""ConnectorConnection authorization and state invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.connectors.contracts import (
    AdapterConnectionStatus,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
)
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
    idempotency_key_digest,
    replay_command,
)
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.iam.authorization import (
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import RoleBindingRecord, ServiceAccountRecord, UserRecord

from .domain import ConnectorConnection
from .errors import ConnectorError
from .management import authorize, map_management_value_error, require_connection
from .models import ConnectorConnectionRecord, ConnectorProviderRecord, ConnectorSetupAttemptRecord


async def authorize_owner_change(
    session: AsyncSession,
    actor: AuthenticatedActor,
    connector: ConnectorProviderRecord,
    owner: PrincipalRef | None,
) -> None:
    if (
        owner is not None
        and owner.principal_type is PrincipalType.user
        and owner.principal_id == actor.principal.principal_id
    ):
        await authorize(session, actor, connector.workspace_id, WorkspaceAction.connector_connection_read)
        user = await session.get(UserRecord, owner.principal_id)
        if user is None or user.status != "active":
            raise ConnectorError("invalid_owner", "ConnectorConnection owner is invalid.", status_code=400)
        return
    await authorize(session, actor, connector.workspace_id, WorkspaceAction.connector_connection_manage)
    if owner is None:
        return
    if owner.principal_type is PrincipalType.user:
        user = await session.get(UserRecord, owner.principal_id)
        workspace_binding = await session.scalar(
            select(RoleBindingRecord.id).where(
                RoleBindingRecord.principal_type == PrincipalType.user.value,
                RoleBindingRecord.principal_id == owner.principal_id,
                RoleBindingRecord.resource_type == "workspace",
                RoleBindingRecord.resource_id == connector.workspace_id,
            )
        )
        if user is None or user.status != "active" or workspace_binding is None:
            raise ConnectorError("invalid_owner", "ConnectorConnection owner is invalid.", status_code=400)
        return
    account = await session.scalar(
        select(ServiceAccountRecord).where(
            ServiceAccountRecord.id == owner.principal_id,
            ServiceAccountRecord.organization_id == connector.organization_id,
            ServiceAccountRecord.workspace_id == connector.workspace_id,
            ServiceAccountRecord.status == "active",
            ServiceAccountRecord.deleted_at.is_(None),
        )
    )
    if account is None:
        raise ConnectorError("invalid_owner", "ConnectorConnection owner is invalid.", status_code=400)


async def authorize_connection(
    session: AsyncSession,
    actor: AuthenticatedActor,
    connection: ConnectorConnectionRecord,
    *,
    mode: Literal["read", "owner_manage", "administrative"],
) -> None:
    if (
        connection.owner_type == actor.principal.principal_type.value
        and connection.owner_id == actor.principal.principal_id
        and actor.principal.principal_type is PrincipalType.user
    ):
        await authorize(session, actor, connection.workspace_id, WorkspaceAction.connector_connection_read)
        return
    if connection.owner_type == PrincipalType.user.value and mode == "owner_manage":
        raise ConnectorError("resource_not_found", "The requested resource was not found.", status_code=404)
    action = WorkspaceAction.connector_connection_read
    if mode != "read" or connection.owner_type is not None:
        action = WorkspaceAction.connector_connection_manage
    await authorize(session, actor, connection.workspace_id, action)


async def has_admin_access(session: AsyncSession, actor: AuthenticatedActor, workspace_id: str) -> bool:
    try:
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.connector_connection_manage,
        )
    except AuthorizationError:
        return False
    return True


async def connection_resource(session: AsyncSession, connection_id: str) -> ConnectorConnection:
    record = await require_connection(session, connection_id)
    return record.to_resource()


def owner_ref(connection: ConnectorConnectionRecord) -> PrincipalRef | None:
    if connection.owner_type is None or connection.owner_id is None:
        return None
    return PrincipalRef(principal_type=PrincipalType(connection.owner_type), principal_id=connection.owner_id)


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
            status_code=409,
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
    if inspection.status is AdapterConnectionStatus.ready:
        connection.catalog_generation += 1
        connection.current_catalog_digest = None
        connection.catalog_available_at = now


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise ConnectorError("version_conflict", "Resource version has changed.", status_code=409)


def idempotency_digest(value: str) -> str:
    try:
        return idempotency_key_digest(value)
    except ConnectivityManagementValueError as error:
        raise map_management_value_error(error) from error


async def replay_connection_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    connection: ConnectorConnectionRecord,
    operation: str,
    key_digest: str,
    request_fingerprint: str,
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
        )
    except ConnectivityManagementValueError as error:
        raise map_management_value_error(error) from error


def external_error(error: ConnectorProviderError) -> ConnectorError:
    if error.retryable or error.outcome_unknown:
        return ConnectorError("connector_unavailable", "ConnectorProvider is unavailable.", status_code=503)
    return ConnectorError("connector_rejected", "ConnectorProvider rejected the operation.", status_code=409)


async def connection_binding(session: AsyncSession, connection: ConnectorConnectionRecord) -> ConnectionBinding:
    attempt = await session.scalar(
        select(ConnectorSetupAttemptRecord).where(
            ConnectorSetupAttemptRecord.connector_connection_id == connection.id,
            ConnectorSetupAttemptRecord.generation == connection.setup_generation,
            ConnectorSetupAttemptRecord.external_ref == connection.external_ref,
        )
    )
    if attempt is None or connection.external_ref is None:
        raise ConnectorError("setup_unavailable", "Verified connection binding is unavailable.", status_code=409)
    return ConnectionBinding(
        external_ref=connection.external_ref,
        connector_key=connection.connector_key,
        external_user_correlation=attempt.external_user_correlation,
    )
