"""Tenant-scoped authorization and relational Environment lookups."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import (
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.secrets.domain import WorkspaceSecretCredential
from a13n_service.secrets.models import SecretRecord

from .domain import EnvironmentCredentialBinding
from .errors import (
    EnvironmentManagementError,
    environment_not_found,
    environment_provider_disabled,
    environment_revision_not_found,
)
from .models import EnvironmentProviderSelectionRecord, EnvironmentRecord, EnvironmentRevisionRecord


async def authorize_environment_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        if error.concealed:
            raise environment_not_found() from error
        raise EnvironmentManagementError("forbidden", "The operation is not allowed.", status_code=403) from error


async def require_provider_selection(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    provider_key: str,
    expected_lock: dict[str, object],
    for_update: bool,
) -> EnvironmentProviderSelectionRecord:
    statement = select(EnvironmentProviderSelectionRecord).where(
        EnvironmentProviderSelectionRecord.organization_id == organization_id,
        EnvironmentProviderSelectionRecord.workspace_id == workspace_id,
        EnvironmentProviderSelectionRecord.provider_key == provider_key,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None or not record.enabled:
        raise environment_provider_disabled()
    if record.provider_lock != expected_lock:
        raise EnvironmentManagementError(
            "environment_provider_lock_changed",
            "The enabled Environment Provider lock no longer matches this deployment.",
            status_code=409,
        )
    return record


async def require_credential_bindings(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[EnvironmentCredentialBinding, ...],
    require_bind_authority: bool,
) -> None:
    workspace_credentials = tuple(
        item.credential for item in bindings if isinstance(item.credential, WorkspaceSecretCredential)
    )
    if workspace_credentials and require_bind_authority:
        await authorize_environment_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.secrets_bind,
        )
    for credential in workspace_credentials:
        available = await session.scalar(
            select(SecretRecord.id).where(
                SecretRecord.id == credential.secret_id,
                SecretRecord.organization_id == organization_id,
                SecretRecord.workspace_id == workspace_id,
                SecretRecord.owner_type == "workspace",
                SecretRecord.owner_id == workspace_id,
                SecretRecord.deleted_at.is_(None),
                SecretRecord.ciphertext.is_not(None),
            )
        )
        if available is None:
            raise EnvironmentManagementError(
                "environment_credential_unavailable",
                "An Environment credential is unavailable.",
                status_code=409,
            )


async def load_environment(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    environment_id: str,
    for_update: bool = False,
) -> EnvironmentRecord:
    statement = select(EnvironmentRecord).where(
        EnvironmentRecord.id == environment_id,
        EnvironmentRecord.organization_id == organization_id,
        EnvironmentRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise environment_not_found()
    return record


async def load_environment_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
    for_update: bool = False,
) -> EnvironmentRevisionRecord:
    statement = select(EnvironmentRevisionRecord).where(
        EnvironmentRevisionRecord.id == revision_id,
        EnvironmentRevisionRecord.organization_id == organization_id,
        EnvironmentRevisionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise environment_revision_not_found()
    return record


__all__ = [
    "authorize_environment_workspace",
    "load_environment",
    "load_environment_revision",
    "require_credential_bindings",
    "require_provider_selection",
]
