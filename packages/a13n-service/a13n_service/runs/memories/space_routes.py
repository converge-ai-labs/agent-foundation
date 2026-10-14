"""Execution-owned Memory Space endpoints, alongside resource configuration routes."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit
from a13n_service.runs.memories import spaces
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import RunPage, SessionPage, SpaceOrganizationStatus
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/memory-spaces", tags=["memory spaces"])


@router.get("/{space_id}/organization", response_model=SpaceOrganizationStatus, openapi_extra={"x-a13n-mcp": True})
async def get_space_organization(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
) -> SpaceOrganizationStatus:
    return await spaces.get_status(runtime.storage, actor, workspace_id, space_id)


@router.get("/{space_id}/organizations", response_model=RunPage, openapi_extra={"x-a13n-mcp": True})
async def list_space_organizations(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> RunPage:
    return await spaces.history(runtime.storage, actor, workspace_id, space_id, limit=limit, cursor=cursor)


@router.post("/{space_id}/organizations", response_model=SpaceOrganizationStatus, status_code=202)
async def organize_space(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
) -> SpaceOrganizationStatus:
    return await spaces.request_organization(runtime.storage, actor, workspace_id, space_id)


@router.get("/{space_id}/sessions", response_model=SessionPage, openapi_extra={"x-a13n-mcp": True})
async def list_space_sessions(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SessionPage:
    return await spaces.associations(runtime.storage, actor, workspace_id, space_id, limit=limit, cursor=cursor)


@router.delete("/{space_id}", status_code=204, openapi_extra={"x-a13n-mcp": True})
async def delete_space(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    space_id: str,
    actor: Actor,
    if_match: IfMatch = None,
) -> Response:
    await spaces.delete_space(runtime.storage, actor, workspace_id, space_id, if_match=if_match)
    return Response(status_code=204)
