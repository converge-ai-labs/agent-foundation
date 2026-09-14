"""Agent reference resolution within the explicit Workspace path."""

from typing import Annotated

from fastapi import Depends, Request

from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_control_runtime

from .errors import agent_not_found


async def resolve_agent(request: Request, workspace_id: WorkspaceId, agent: str) -> str:
    control = get_control_runtime(request)
    if control is None:
        raise agent_not_found()
    return await control.agents.queries.resolve_reference(workspace_id=workspace_id, reference=agent)


AgentId = Annotated[str, Depends(resolve_agent)]
