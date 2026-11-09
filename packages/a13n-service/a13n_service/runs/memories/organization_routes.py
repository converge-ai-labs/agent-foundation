"""Composable organization requests; execution control stays on the ordinary Run API."""

from fastapi import APIRouter

from a13n_service.infra.http import PageLimit
from a13n_service.runs.memories import candidates, organizations
from a13n_service.runs.memories.organization_schemas import (
    CandidateApply,
    CandidateDiff,
    MemoryOrganization,
    MemoryOrganizationPage,
    OrganizationCandidate,
    OrganizationRequest,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/memory-organizations", tags=["memory organizations"])


@router.post("", response_model=MemoryOrganization, status_code=202)
async def create_memory_organization(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    body: OrganizationRequest,
) -> MemoryOrganization:
    return await organizations.create(runtime, actor, workspace_id, body)


@router.get("", response_model=MemoryOrganizationPage, openapi_extra={"x-a13n-mcp": True})
async def list_memory_organizations(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemoryOrganizationPage:
    return await organizations.listing(runtime, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{organization_id}", response_model=MemoryOrganization, openapi_extra={"x-a13n-mcp": True})
async def get_memory_organization(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    organization_id: str,
    actor: Actor,
) -> MemoryOrganization:
    return await organizations.get(runtime, actor, workspace_id, organization_id)


@router.get("/{organization_id}/candidate", response_model=OrganizationCandidate, openapi_extra={"x-a13n-mcp": True})
async def get_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor
) -> OrganizationCandidate:
    return await candidates.get(runtime, actor, workspace_id, organization_id)


@router.get("/{organization_id}/candidate/diff", response_model=CandidateDiff, openapi_extra={"x-a13n-mcp": True})
async def get_organization_candidate_diff(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor, path: str
) -> CandidateDiff:
    return await candidates.diff(runtime, actor, workspace_id, organization_id, path)


@router.post("/{organization_id}/candidate/apply", response_model=OrganizationCandidate)
async def apply_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor, body: CandidateApply
) -> OrganizationCandidate:
    return await candidates.apply(runtime, actor, workspace_id, organization_id, body)


@router.post("/{organization_id}/candidate/discard", response_model=OrganizationCandidate)
async def discard_organization_candidate(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, organization_id: str, actor: Actor
) -> OrganizationCandidate:
    return await candidates.discard(runtime, actor, workspace_id, organization_id)
