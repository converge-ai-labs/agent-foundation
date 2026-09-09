"""Profile, permission and audit views for the Console."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from a13n_service.etags import resource_etag
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId

from ..profile_schemas import OrganizationPermissions, Permissions, SecurityEvent, UpdateProfileRequest
from ..schemas import (
    ApiKey,
    Organization,
    Page,
    RoleBinding,
    SetRoleRequest,
    UpdateResourceProfileRequest,
    User,
)
from ..service_common import not_found
from .dependencies import Actor, Pagination, identity, private_response

router = APIRouter(prefix="/api/v1", tags=["identity-settings"], dependencies=[Depends(private_response)])
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


@router.patch("/users/me", response_model=User)
async def update_profile(
    request: Request, response: Response, actor: Actor, body: UpdateProfileRequest, if_match: IfMatch
) -> User:
    user = await identity(request).profiles.update_user(actor, body.name, if_match)
    response.headers["ETag"] = resource_etag(user.id, user.updated_at)
    return user


@router.get("/organizations/{organization}", response_model=Organization)
async def organization(
    request: Request, response: Response, actor: Actor, organization_id: OrganizationId
) -> Organization:
    result = await identity(request).membership.organization(actor)
    if result.id != organization_id:
        raise not_found()
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/organizations/{organization}", response_model=Organization)
async def update_organization(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    body: UpdateResourceProfileRequest,
    if_match: IfMatch,
) -> Organization:
    result = await identity(request).profiles.update_organization(
        actor, organization_id, body.name, if_match, key=body.key
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/workspaces/{workspace}/permissions", response_model=Permissions)
async def workspace_permissions(request: Request, actor: Actor, workspace_id: WorkspaceId) -> Permissions:
    return await identity(request).profiles.permissions(actor, workspace_id)


@router.get("/organizations/{organization}/permissions", response_model=OrganizationPermissions)
async def organization_permissions(
    request: Request, actor: Actor, organization_id: OrganizationId
) -> OrganizationPermissions:
    return await identity(request).profiles.organization_permissions(actor, organization_id)


@router.get("/workspaces/{workspace}/members", response_model=Page[User])
async def workspace_members(request: Request, actor: Actor, workspace_id: WorkspaceId, page: Pagination) -> Page[User]:
    return await identity(request).profiles.members(actor, workspace_id, page)


@router.get("/workspaces/{workspace}/api-keys", response_model=Page[ApiKey])
async def workspace_member_keys(
    request: Request, actor: Actor, workspace_id: WorkspaceId, page: Pagination
) -> Page[ApiKey]:
    return await identity(request).profiles.member_keys(actor, workspace_id, page)


@router.post("/organizations/{organization}/role-bindings", status_code=201, response_model=RoleBinding)
async def create_organization_binding(
    request: Request, response: Response, actor: Actor, organization_id: OrganizationId, body: SetRoleRequest
) -> RoleBinding:
    result = await identity(request).membership.create_binding(actor, organization_id, body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/users/me/security-activity", response_model=Page[SecurityEvent])
async def personal_security_activity(request: Request, actor: Actor, page: Pagination) -> Page[SecurityEvent]:
    return await identity(request).profiles.security_events(actor, page)


@router.get("/organizations/{organization}/security-audit-events", response_model=Page[SecurityEvent])
async def organization_security_events(
    request: Request, actor: Actor, organization_id: OrganizationId, page: Pagination
) -> Page[SecurityEvent]:
    return await identity(request).profiles.security_events(actor, page, organization_id=organization_id)


@router.get("/workspaces/{workspace}/security-audit-events", response_model=Page[SecurityEvent])
async def workspace_security_events(
    request: Request, actor: Actor, workspace_id: WorkspaceId, page: Pagination
) -> Page[SecurityEvent]:
    return await identity(request).profiles.security_events(actor, page, workspace_id=workspace_id)
