"""Authorization and tenant-consistent scope checks for managed Hooks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord

from .domain import CreateHookSubscriptionRequest
from .errors import HookManagementError


async def authorize_hook(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    try:
        return await authorize_workspace(database, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise HookManagementError(
            "resource_not_found" if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            category=ErrorCategory.not_found if error.concealed else ErrorCategory.forbidden,
        ) from error


async def validate_hook_scope(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    request: CreateHookSubscriptionRequest,
) -> None:
    session_id = request.session_id
    thread_id = request.thread_id
    run_id = request.run_id
    if session_id is not None and not await _session_is_in_workspace(
        database,
        organization_id=organization_id,
        workspace_id=workspace_id,
        session_id=session_id,
    ):
        raise _invalid_scope()
    if thread_id is not None:
        thread = await database.scalar(
            select(ThreadRecord).where(
                ThreadRecord.tenant_id == organization_id,
                ThreadRecord.id == thread_id,
            )
        )
        if (
            thread is None
            or (session_id is not None and thread.session_id != session_id)
            or not await _session_is_in_workspace(
                database,
                organization_id=organization_id,
                workspace_id=workspace_id,
                session_id=thread.session_id,
            )
        ):
            raise _invalid_scope()
    if run_id is not None:
        run = await database.scalar(
            select(RunRecord).where(RunRecord.tenant_id == organization_id, RunRecord.id == run_id)
        )
        if (
            run is None
            or (session_id is not None and run.session_id != session_id)
            or (thread_id is not None and run.thread_id != thread_id)
            or not await _session_is_in_workspace(
                database,
                organization_id=organization_id,
                workspace_id=workspace_id,
                session_id=run.session_id,
            )
        ):
            raise _invalid_scope()


async def _session_is_in_workspace(
    database: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    session_id: str,
) -> bool:
    found = await database.scalar(
        select(SessionRecord.id).where(
            SessionRecord.tenant_id == organization_id,
            SessionRecord.id == session_id,
            SessionRecord.workspace_id == workspace_id,
        )
    )
    return found is not None


def _invalid_scope() -> HookManagementError:
    return HookManagementError(
        "invalid_hook_scope",
        "The Hook subscription scope is not tenant-consistent.",
        category=ErrorCategory.invalid_request,
    )


__all__ = ["authorize_hook", "validate_hook_scope"]
