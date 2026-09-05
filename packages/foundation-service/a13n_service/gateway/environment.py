"""Read-only Environment input preview; Run acceptance owns final selection."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import EnvironmentSelection, ExistingEnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.errors import invalid_environment
from a13n_service.environments.models import (
    EnvironmentRecord,
    EnvironmentTemplateRecord,
    EnvironmentTemplateRevisionRecord,
)
from a13n_service.environments.selection import Omitted
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
    if isinstance(choice, NewEnvironmentSelection):
        template = await database.scalar(
            select(EnvironmentTemplateRecord).where(
                EnvironmentTemplateRecord.id == choice.template_id,
                EnvironmentTemplateRecord.workspace_id == actor.boundary_workspace_id,
                EnvironmentTemplateRecord.archived_at.is_(None),
            )
        )
        if template is None:
            raise invalid_environment("Environment template is unavailable")
        selected = await database.scalar(
            select(EnvironmentTemplateRevisionRecord).where(
                EnvironmentTemplateRevisionRecord.template_id == template.id,
                EnvironmentTemplateRevisionRecord.version == (choice.version or template.version),
            )
        )
    else:
        selected = await database.scalar(
            select(EnvironmentRecord).where(
                EnvironmentRecord.id == choice.environment_id,
                EnvironmentRecord.workspace_id == actor.boundary_workspace_id,
            )
        )
    if selected is None:
        raise invalid_environment("Environment selection is unavailable")
    access = selected.to_resource().access
    ranks = {"read_only": 0, "read_write": 1, "full": 2}
    return min((access, access_ceiling or access), key=ranks.__getitem__)
