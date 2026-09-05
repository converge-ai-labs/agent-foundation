"""Read-only Environment input preview; Run acceptance owns final selection."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.environments.domain import EnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.selection import Omitted, intersect_access, resolve_selection
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent

from .environment_selection import resolve_environment_intent


async def input_environment_access(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    choice: EnvironmentSelection | Omitted | None,
    inherited_id: str | Omitted | None = Omitted.UNSET,
    access_ceiling: str | None = None,
) -> str | None:
    choice = await resolve_environment_intent(database, agent_id=agent_id, choice=choice, inherited_id=inherited_id)
    if choice is None:
        return None
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
    selected = await resolve_selection(database, workspace_id=actor.workspace_id, choice=choice)
    return intersect_access(selected.to_resource().access, access_ceiling)
