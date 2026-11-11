"""Credential administration; writes are never admitted to Run platform tools."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.egress_credentials import service
from a13n_service.resources.egress_credentials.schemas import (
    Credential,
    CredentialCreate,
    CredentialPage,
    CredentialRotation,
    CredentialUpdate,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/egress-credentials", tags=["egress credentials"])


@router.post("", response_model=Credential, status_code=201)
async def create_egress_credential(
    response: Response, workspace_id: WorkspaceId, body: CredentialCreate, actor: Actor, runtime: CurrentRuntime
) -> Credential:
    return tagged(response, await service.create_credential(runtime.storage, runtime.keys, actor, workspace_id, body))


@router.get("", response_model=CredentialPage, openapi_extra={"x-a13n-mcp": True})
async def list_egress_credentials(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> CredentialPage:
    return await service.list_credentials(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get("/{credential_id}", response_model=Credential, openapi_extra={"x-a13n-mcp": True})
async def get_egress_credential(
    response: Response, workspace_id: WorkspaceId, credential_id: str, actor: Actor, runtime: CurrentRuntime
) -> Credential:
    return tagged(response, await service.get_credential(runtime.storage, actor, workspace_id, credential_id))


@router.patch("/{credential_id}", response_model=Credential)
async def update_egress_credential(
    response: Response,
    workspace_id: WorkspaceId,
    credential_id: str,
    body: CredentialUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Credential:
    return tagged(
        response,
        await service.update_credential(runtime.storage, actor, workspace_id, credential_id, body, if_match=if_match),
    )


@router.put("/{credential_id}/value", response_model=Credential, status_code=202)
async def rotate_egress_credential(
    response: Response,
    workspace_id: WorkspaceId,
    credential_id: str,
    body: CredentialRotation,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Credential:
    return tagged(
        response,
        await service.rotate_credential(
            runtime.storage,
            runtime.keys,
            actor,
            workspace_id,
            credential_id,
            body,
            if_match=if_match,
            redis=runtime.redis,
        ),
    )


@router.post("/{credential_id}/revoke", response_model=Credential, status_code=202)
async def revoke_egress_credential(
    response: Response,
    workspace_id: WorkspaceId,
    credential_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Credential:
    return tagged(
        response,
        await service.revoke_credential(
            runtime.storage, actor, workspace_id, credential_id, if_match=if_match, redis=runtime.redis
        ),
    )


@router.post("/{credential_id}/sync", response_model=Credential, status_code=202, openapi_extra={"x-a13n-mcp": False})
async def sync_egress_credential(
    response: Response, workspace_id: WorkspaceId, credential_id: str, actor: Actor, runtime: CurrentRuntime
) -> Credential:
    return tagged(
        response,
        await service.request_sync(runtime.storage, actor, workspace_id, credential_id, redis=runtime.redis),
    )
