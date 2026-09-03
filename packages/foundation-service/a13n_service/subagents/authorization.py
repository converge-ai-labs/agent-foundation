"""Shared current-Principal authorization for child Run operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.domain import Run


class ChildRunAuthorizationError(RuntimeError):
    """The persisted parent Principal no longer authorizes a child operation."""


async def authorize_parent_child_action(
    database: AsyncSession,
    *,
    parent: Run,
    child_agent_ids: tuple[str, ...],
    workspace_id: str,
    action: WorkspaceAction,
) -> None:
    actor = AuthenticatedActor(
        principal=parent.authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{parent.id}",
        boundary_workspace_id=workspace_id,
        request_id=parent.id,
    )
    try:
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=parent.agent_id,
            action=WorkspaceAction.run_read,
        )
        for child_agent_id in child_agent_ids:
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=child_agent_id,
                action=action,
            )
    except AuthorizationError as error:
        raise ChildRunAuthorizationError from error


__all__ = [
    "ChildRunAuthorizationError",
    "authorize_parent_child_action",
]
