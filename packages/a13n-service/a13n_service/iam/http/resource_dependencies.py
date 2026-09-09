"""Resolve path references before calling use cases with immutable identifiers."""

from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request

from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import short_session

from .. import references
from ..domain import AuthenticatedActor
from ..service_common import not_found
from .authentication import authenticate_request

Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


async def resolve_workspace(request: Request, actor: Actor, workspace: str) -> str:
    runtime = get_process_runtime(request)
    if runtime is None:
        raise not_found()
    async with short_session(runtime.shared.storage.sessions) as session:
        return await references.workspace_id(session, actor, workspace)


async def resolve_organization(request: Request, actor: Actor, organization: str) -> str:
    runtime = get_process_runtime(request)
    if runtime is None:
        raise not_found()
    async with short_session(runtime.shared.storage.sessions) as session:
        return await references.organization_id(session, actor, organization)


WorkspaceId = Annotated[str, Depends(resolve_workspace)]
OrganizationId = Annotated[str, Depends(resolve_organization)]


async def workspace_actor(actor: Actor, workspace_id: WorkspaceId) -> AuthenticatedActor:
    return replace(actor, boundary_workspace_id=workspace_id, boundary_organization_id=None)
