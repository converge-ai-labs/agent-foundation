"""Workspace-admin Cloudflare account operations, outside the MCP management surface."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, tagged
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

from . import service
from .schemas import Deployment, DeploymentCreate, DeploymentList, DeploymentRetry

router = APIRouter(prefix="/api/v1/cloudflare-deployments", tags=["cloudflare-deployments"])


@router.get("")
async def list_cloudflare_deployments(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> DeploymentList:
    return await service.list_deployments(runtime, actor, workspace_id)


@router.get("/{deployment_id}")
async def get_cloudflare_deployment(
    response: Response, workspace_id: WorkspaceId, deployment_id: str, actor: Actor, runtime: CurrentRuntime
) -> Deployment:
    return tagged(response, await service.get_deployment(runtime, actor, workspace_id, deployment_id))


@router.post("", status_code=202)
async def create_cloudflare_deployment(
    response: Response, workspace_id: WorkspaceId, body: DeploymentCreate, actor: Actor, runtime: CurrentRuntime
) -> Deployment:
    return tagged(response, await service.create(runtime, actor, workspace_id, body))


@router.post("/{deployment_id}/retry", status_code=202)
async def retry_cloudflare_deployment(
    response: Response,
    workspace_id: WorkspaceId,
    deployment_id: str,
    body: DeploymentRetry,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Deployment:
    return tagged(response, await service.retry(runtime, actor, workspace_id, deployment_id, body, if_match))


@router.post("/{deployment_id}/retire", status_code=202)
async def retire_cloudflare_deployment(
    response: Response,
    workspace_id: WorkspaceId,
    deployment_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Deployment:
    return tagged(response, await service.retire(runtime, actor, workspace_id, deployment_id, if_match))
