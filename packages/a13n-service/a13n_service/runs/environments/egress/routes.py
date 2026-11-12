"""Nonsecret Session application evidence for credential and Environment pages."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import PageLimit
from a13n_service.runs.environments.egress import queries
from a13n_service.runs.environments.egress.queries import SessionCredentialPage
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["egress credentials"])


@router.get(
    "/egress-credentials/{credential_id}/sessions",
    response_model=SessionCredentialPage,
    openapi_extra={"x-a13n-mcp": True},
)
async def list_credential_sessions(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    credential_id: str,
    response: Response,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SessionCredentialPage:
    response.headers["Cache-Control"] = "no-store"
    return await queries.credential_sessions(
        runtime.storage, actor, workspace_id, credential_id, limit=limit, cursor=cursor
    )


@router.get(
    "/environments/{environment_id}/credential-sessions",
    response_model=SessionCredentialPage,
    openapi_extra={"x-a13n-mcp": True},
)
async def list_environment_credential_sessions(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    environment_id: str,
    response: Response,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SessionCredentialPage:
    response.headers["Cache-Control"] = "no-store"
    return await queries.environment_sessions(
        runtime.storage, actor, workspace_id, environment_id, limit=limit, cursor=cursor
    )
