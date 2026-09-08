"""Thin identity management routes using the shared authenticated actor."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response

from a13n_service.etags import resource_etag

from .auth_router import Actor, Pagination, identity, private_response
from .schemas import (
    ApiKey,
    CreatedKey,
    CreateInvitationRequest,
    CreateKeyRequest,
    CreateServiceAccountRequest,
    CreateWorkspaceRequest,
    ExpectedVersion,
    Grant,
    Invitation,
    InvitationDelivery,
    InviteWorkspaceRequest,
    Organization,
    Page,
    RequestModel,
    RoleBinding,
    ServiceAccount,
    SetRoleRequest,
    UpdateServiceAccountRequest,
    User,
    Workspace,
)

router = APIRouter(prefix="/api/v1", tags=["identity-management"], dependencies=[Depends(private_response)])
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


@router.get("/organizations", response_model=Page[Organization])
async def organizations(request: Request, actor: Actor) -> Page[Organization]:
    return Page(items=[await identity(request).membership.organization(actor)], next_cursor=None)


@router.get("/organizations/{organization_id}/workspaces", response_model=Page[Workspace])
async def workspaces(request: Request, actor: Actor, organization_id: str, page: Pagination) -> Page[Workspace]:
    return await identity(request).membership.workspaces(actor, organization_id, page)


@router.post("/organizations/{organization_id}/workspaces", status_code=201, response_model=Workspace)
async def create_workspace(
    request: Request, response: Response, actor: Actor, organization_id: str, body: CreateWorkspaceRequest
) -> Workspace:
    result = await identity(request).membership.create_workspace(actor, organization_id, body.name)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/workspaces/{workspace_id}", response_model=Workspace)
async def workspace(request: Request, response: Response, actor: Actor, workspace_id: str) -> Workspace:
    result = await identity(request).membership.workspace(actor, workspace_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/workspaces/{workspace_id}", response_model=Workspace)
async def rename_workspace(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateWorkspaceRequest,
    if_match: IfMatch,
) -> Workspace:
    result = await identity(request).membership.update_workspace(actor, workspace_id, body.name, if_match)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(request: Request, actor: Actor, workspace_id: str, if_match: IfMatch) -> None:
    await identity(request).membership.delete_workspace(actor, workspace_id, if_match)


@router.get("/organizations/{organization_id}/users", response_model=Page[User])
async def users(request: Request, actor: Actor, organization_id: str, page: Pagination) -> Page[User]:
    return await identity(request).collections.users(actor, organization_id, page)


@router.get("/organizations/{organization_id}/invitations", response_model=Page[Invitation])
async def invitations(request: Request, actor: Actor, organization_id: str, page: Pagination) -> Page[Invitation]:
    return await identity(request).collections.invitations(actor, organization_id, page)


@router.post("/organizations/{organization_id}/invitations", status_code=201, response_model=InvitationDelivery)
async def invite(
    request: Request, actor: Actor, organization_id: str, body: CreateInvitationRequest
) -> InvitationDelivery:
    return await identity(request).invitations.create(actor, organization_id, body)


@router.get("/workspaces/{workspace_id}/invitations", response_model=Page[Invitation])
async def workspace_invitations(
    request: Request, actor: Actor, workspace_id: str, page: Pagination
) -> Page[Invitation]:
    workspace = await identity(request).membership.workspace(actor, workspace_id)
    return await identity(request).collections.invitations(
        actor, workspace.organization_id, page, workspace_id=workspace_id
    )


@router.post("/workspaces/{workspace_id}/invitations", status_code=201, response_model=InvitationDelivery)
async def invite_to_workspace(
    request: Request, actor: Actor, workspace_id: str, body: InviteWorkspaceRequest
) -> InvitationDelivery:
    runtime = identity(request)
    workspace = await runtime.membership.workspace(actor, workspace_id)
    return await runtime.invitations.create(
        actor,
        workspace.organization_id,
        CreateInvitationRequest(
            email=body.email,
            grants=[Grant(resource_type="workspace", resource_id=workspace_id, role_key=body.role)],
        ),
    )


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationDelivery)
async def resend_invitation(
    request: Request, actor: Actor, invitation_id: str, body: ExpectedVersion
) -> InvitationDelivery:
    return await identity(request).invitations.resend(actor, invitation_id, body.expected_version)


@router.post("/invitations/{invitation_id}/revoke", response_model=Invitation)
async def revoke_invitation(request: Request, actor: Actor, invitation_id: str, body: ExpectedVersion) -> Invitation:
    return await identity(request).invitations.revoke(actor, invitation_id, body.expected_version)


@router.get("/workspaces/{workspace_id}/personal-api-keys", response_model=Page[ApiKey])
async def personal_keys(request: Request, actor: Actor, workspace_id: str, page: Pagination) -> Page[ApiKey]:
    return await identity(request).collections.keys(actor, workspace_id, page)


@router.post("/workspaces/{workspace_id}/personal-api-keys", status_code=201, response_model=CreatedKey)
async def create_personal_key(
    request: Request, response: Response, actor: Actor, workspace_id: str, body: CreateKeyRequest
) -> CreatedKey:
    response.headers["Cache-Control"] = "no-store"
    return await identity(request).keys.create(actor, workspace_id, body)


@router.get("/api-keys/{key_id}", response_model=ApiKey)
async def key_metadata(request: Request, actor: Actor, key_id: str) -> ApiKey:
    return await identity(request).keys.get(actor, key_id)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKey)
async def revoke_key(request: Request, actor: Actor, key_id: str) -> ApiKey:
    return await identity(request).keys.revoke(actor, key_id)


@router.get("/workspaces/{workspace_id}/service-accounts", response_model=Page[ServiceAccount])
async def accounts(request: Request, actor: Actor, workspace_id: str, page: Pagination) -> Page[ServiceAccount]:
    return await identity(request).collections.accounts(actor, workspace_id, page)


@router.post("/workspaces/{workspace_id}/service-accounts", status_code=201, response_model=ServiceAccount)
async def create_account(
    request: Request, actor: Actor, workspace_id: str, body: CreateServiceAccountRequest
) -> ServiceAccount:
    return await identity(request).accounts.create(actor, workspace_id, body)


@router.get("/service-accounts/{account_id}", response_model=ServiceAccount)
async def account(request: Request, actor: Actor, account_id: str) -> ServiceAccount:
    return await identity(request).accounts.get(actor, account_id)


@router.patch("/service-accounts/{account_id}", response_model=ServiceAccount)
async def update_account(
    request: Request, actor: Actor, account_id: str, body: UpdateServiceAccountRequest
) -> ServiceAccount:
    return await identity(request).accounts.update(actor, account_id, body)


@router.delete("/service-accounts/{account_id}", status_code=204)
async def delete_account(request: Request, actor: Actor, account_id: str, body: ExpectedVersion) -> None:
    await identity(request).accounts.delete(actor, account_id, body.expected_version)


@router.get("/service-accounts/{account_id}/api-keys", response_model=Page[ApiKey])
async def account_keys(request: Request, actor: Actor, account_id: str, page: Pagination) -> Page[ApiKey]:
    runtime = identity(request)
    account = await runtime.accounts.get(actor, account_id)
    return await runtime.collections.keys(actor, account.workspace_id, page, account_id=account_id)


@router.post("/service-accounts/{account_id}/api-keys", status_code=201, response_model=CreatedKey)
async def create_account_key(
    request: Request, response: Response, actor: Actor, account_id: str, body: CreateKeyRequest
) -> CreatedKey:
    runtime = identity(request)
    account = await runtime.accounts.get(actor, account_id)
    response.headers["Cache-Control"] = "no-store"
    return await runtime.keys.create(actor, account.workspace_id, body, service_account_id=account_id)


@router.get("/organizations/{organization_id}/role-bindings", response_model=Page[RoleBinding])
async def organization_roles(
    request: Request, actor: Actor, organization_id: str, page: Pagination
) -> Page[RoleBinding]:
    return await identity(request).collections.bindings(actor, organization_id, page)


@router.get("/workspaces/{workspace_id}/role-bindings", response_model=Page[RoleBinding])
async def workspace_roles(request: Request, actor: Actor, workspace_id: str, page: Pagination) -> Page[RoleBinding]:
    workspace = await identity(request).membership.workspace(actor, workspace_id)
    return await identity(request).collections.bindings(
        actor, workspace.organization_id, page, workspace_id=workspace_id
    )


@router.post("/workspaces/{workspace_id}/role-bindings", status_code=201, response_model=RoleBinding)
async def add_workspace_member(
    request: Request, response: Response, actor: Actor, workspace_id: str, body: SetRoleRequest
) -> RoleBinding:
    runtime = identity(request)
    workspace = await runtime.membership.workspace(actor, workspace_id)
    result = await runtime.membership.create_binding(actor, workspace.organization_id, body, workspace_id=workspace_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


class ChangeRoleRequest(RequestModel):
    role: Literal["member", "viewer", "runner", "builder", "admin"]


@router.get("/role-bindings/{binding_id}", response_model=RoleBinding)
async def role_binding(request: Request, response: Response, actor: Actor, binding_id: str) -> RoleBinding:
    result = await identity(request).membership.binding(actor, binding_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/role-bindings/{binding_id}", response_model=RoleBinding)
async def change_role(
    request: Request, response: Response, actor: Actor, binding_id: str, body: ChangeRoleRequest, if_match: IfMatch
) -> RoleBinding:
    result = await identity(request).membership.change_binding(actor, binding_id, if_match, role=body.role)
    assert result is not None
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.delete("/role-bindings/{binding_id}", status_code=204)
async def remove_member(request: Request, actor: Actor, binding_id: str, if_match: IfMatch) -> None:
    await identity(request).membership.change_binding(actor, binding_id, if_match, role=None)
