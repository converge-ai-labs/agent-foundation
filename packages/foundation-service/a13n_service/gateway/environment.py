"""Read-only Environment input preview; Run acceptance owns final selection."""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import EnvironmentSelection, ExistingEnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.selection import Omitted, intersect_access, resolve_selection
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent


async def input_environment_access(
    database: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    choice: EnvironmentSelection | Omitted | None,
    inherited_id: str | Omitted | None = Omitted.UNSET,
    access_ceiling: str | None = None,
) -> str | None:
    if choice is Omitted.UNSET:
        if inherited_id is Omitted.UNSET:
            agent = await database.get(AgentRecord, agent_id)
            choice = (
                NewEnvironmentSelection(template_id=agent.default_environment_template_id)
                if agent and agent.default_environment_template_id
                else None
            )
        else:
            choice = ExistingEnvironmentSelection(environment_id=inherited_id) if inherited_id else None
    if choice is None:
        return None
    await authorize_agent(
        database,
        actor=actor,
        workspace_id=actor.boundary_workspace_id,
        agent_id=agent_id,
        action=(
            WorkspaceAction.environment_template_use
            if isinstance(choice, NewEnvironmentSelection)
            else WorkspaceAction.environment_use
        ),
    )
    selected = await resolve_selection(database, workspace_id=actor.boundary_workspace_id, choice=choice)
    return intersect_access(selected.to_resource().access, access_ceiling)
