"""Read-only Environment input preview; Run acceptance owns final selection."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.environments.domain import EnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.selection import Omitted, resolve_selection
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent

from .environment_selection import resolve_requested_environment


async def has_input_environment(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    agent_revision_id: str | None = None,
    choice: EnvironmentSelection | Omitted | None,
    inherited_id: str | Omitted | None = Omitted.UNSET,
) -> bool:
    choice = await resolve_requested_environment(
        database,
        agent_id=agent_id,
        agent_revision_id=agent_revision_id,
        choice=choice,
        inherited_id=inherited_id,
    )
    if choice is None:
        return False
    await authorize_agent(
        database,
        actor=actor,
        workspace_id=actor.workspace_id,
        agent_id=agent_id,
        action=(
            WorkspaceAction.environment_template_use
            if isinstance(choice, NewEnvironmentSelection)
            else WorkspaceAction.environment_use
        ),
    )
    await resolve_selection(database, workspace_id=actor.workspace_id, choice=choice)
    return True
