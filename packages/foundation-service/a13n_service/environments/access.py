"""Tenant-scoped authorization and relational Environment lookups."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import (
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_workspace,
)

from .errors import (
    EnvironmentManagementError,
    environment_not_found,
)


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
