"""Composable organization requests; execution control stays on the ordinary Run API."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.memories.schemas import OrganizationPolicy
from a13n_service.runs.memories import automation, candidates, organizations
from a13n_service.runs.memories.organization_schemas import (
    CandidateApply,
    CandidateDiff,
    MemoryAutomation,
    MemoryOrganization,
    MemoryOrganizationPage,
    OrganizationCandidate,
    OrganizationRequest,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["memory organizations"])


@router.post("/memory-organizations", response_model=MemoryOrganization, status_code=202)
async def create_memory_organization(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    body: OrganizationRequest,
) -> MemoryOrganization:
    return await organizations.create(runtime, actor, workspace_id, body)


@router.get("/memory-organizations", response_model=MemoryOrganizationPage, openapi_extra={"x-a13n-mcp": True})
async def list_memory_organizations(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemoryOrganizationPage:
    return await organizations.listing(runtime, actor, workspace_id, limit=limit, cursor=cursor)


@router.get(
    "/memory-organizations/{organization_id}", response_model=MemoryOrganization, openapi_extra={"x-a13n-mcp": True}
)
async def get_memory_organization(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    organization_id: str,
    actor: Actor,
) -> MemoryOrganization:
    return await organizations.get(runtime, actor, workspace_id, organization_id)


@router.get(
    "/memory-organizations/{organization_id}/candidate",
    response_model=OrganizationCandidate,
    openapi_extra={"x-a13n-mcp": True},
)
async def get_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor
) -> OrganizationCandidate:
    return await candidates.get(runtime, actor, workspace_id, organization_id)


@router.get(
    "/memory-organizations/{organization_id}/candidate/diff",
    response_model=CandidateDiff,
    openapi_extra={"x-a13n-mcp": True},
)
async def get_organization_candidate_diff(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor, path: str
) -> CandidateDiff:
    return await candidates.diff(runtime, actor, workspace_id, organization_id, path)


@router.post("/memory-organizations/{organization_id}/candidate/apply", response_model=OrganizationCandidate)
async def apply_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor, body: CandidateApply
) -> OrganizationCandidate:
    return await candidates.apply(runtime, actor, workspace_id, organization_id, body)


@router.post("/memory-organizations/{organization_id}/candidate/discard", response_model=OrganizationCandidate)
async def discard_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor
) -> OrganizationCandidate:
    return await candidates.discard(runtime, actor, workspace_id, organization_id)


@router.get("/memories/{memory_id}/organization", response_model=MemoryAutomation, openapi_extra={"x-a13n-mcp": True})
async def get_memory_automation(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, memory_id: str, actor: Actor, response: Response
) -> MemoryAutomation:
    return tagged(response, await automation.get(runtime, actor, workspace_id, memory_id))


@router.put("/memories/{memory_id}/organization", response_model=MemoryAutomation)
async def update_memory_automation(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    memory_id: str,
    actor: Actor,
    body: OrganizationPolicy,
    response: Response,
    if_match: IfMatch = None,
) -> MemoryAutomation:
    return tagged(response, await automation.update(runtime, actor, workspace_id, memory_id, body, if_match=if_match))
