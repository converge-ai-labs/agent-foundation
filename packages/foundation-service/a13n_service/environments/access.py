"""Organization-scoped authorization and relational Environment lookups."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import (
    AuthorizationError,
    WorkspaceAction,
)
from a13n_service.iam.resource_scope import ResourceScope, actor_scope, authorize_resource, authorize_scope

from .errors import (
    EnvironmentManagementError,
    environment_not_found,
)


async def authorize_environment_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    action: WorkspaceAction,
) -> ResourceScope:
    try:
        return await authorize_scope(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        if error.concealed:
            raise environment_not_found() from error
        raise EnvironmentManagementError(
            "forbidden", "The operation is not allowed.", category=ErrorCategory.forbidden
        ) from error


async def environment_actor_scope(session: AsyncSession, actor: AuthenticatedActor) -> ResourceScope:
    try:
        return await actor_scope(session, actor)
    except AuthorizationError as error:
        raise environment_not_found() from error


async def authorize_environment_resource(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    action: WorkspaceAction,
    manage: bool = False,
) -> None:
    try:
        await authorize_resource(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=action,
            manage=manage,
        )
    except AuthorizationError as error:
        raise environment_not_found() from error
