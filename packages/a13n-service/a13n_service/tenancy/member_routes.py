"""Who belongs where over HTTP: an organization's members, grants and invitations at organization or workspace
scope, service accounts and the API keys confined to a workspace."""

from typing import Literal

from fastapi import APIRouter, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.tenancy import api_keys, grants, invitations, service_accounts, users
from a13n_service.tenancy.access import OrganizationPath, WorkspacePath
from a13n_service.tenancy.requests import Actor, CurrentRuntime
from a13n_service.tenancy.schemas import (
    ApiKey,
    ApiKeyPage,
    GrantCreate,
    GrantPage,
    GrantUpdate,
    GrantView,
    Invitation,
    InvitationCreate,
    InvitationPage,
    InvitationReceipt,
    IssuedKey,
    KeyCreate,
    MemberPage,
    MemberPasswordReset,
    PasswordResetReceipt,
    ServiceAccount,
    ServiceAccountCreate,
    ServiceAccountPage,
    ServiceAccountUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["tenancy"])

ORGANIZATION = "/organizations/{organization_id}"
WORKSPACE = "/workspaces/{workspace_id}"


@router.get(ORGANIZATION + "/members", response_model=MemberPage)
async def list_members(
    organization_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    kind: Literal["user", "service_account"] | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> MemberPage:
    return await grants.list_members(
        runtime.storage, runtime.access, actor, organization_id, kind=kind, limit=limit, cursor=cursor
    )


@router.post(ORGANIZATION + "/members/{user_id}/password-reset", response_model=PasswordResetReceipt)
async def reset_member_password(
    organization_id: str, user_id: str, body: MemberPasswordReset, actor: Actor, runtime: CurrentRuntime
) -> PasswordResetReceipt:
    return await users.reset_member_password(
        runtime.storage, runtime.access, runtime.keys, runtime.settings, actor, organization_id, user_id, body.delivery
    )


@router.get(ORGANIZATION + "/grants", response_model=GrantPage)
async def list_organization_grants(
    organization_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> GrantPage:
    path = OrganizationPath(organization_id)
    return await grants.list_grants(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)


@router.post(ORGANIZATION + "/grants", response_model=GrantView, status_code=201)
async def create_organization_grant(
    organization_id: str, body: GrantCreate, actor: Actor, runtime: CurrentRuntime
) -> GrantView:
    return await grants.create_grant(runtime.storage, runtime.access, actor, OrganizationPath(organization_id), body)


@router.patch(ORGANIZATION + "/grants/{grant_id}", response_model=GrantView)
async def change_organization_grant(
    organization_id: str, grant_id: str, body: GrantUpdate, actor: Actor, runtime: CurrentRuntime
) -> GrantView:
    """The grant is replaced: the result carries its new ID."""
    path = OrganizationPath(organization_id)
    return await grants.change_role(runtime.storage, runtime.access, actor, path, grant_id, body)


@router.delete(ORGANIZATION + "/grants/{grant_id}", status_code=204)
async def delete_organization_grant(organization_id: str, grant_id: str, actor: Actor, runtime: CurrentRuntime) -> None:
    await grants.delete_grant(runtime.storage, runtime.access, actor, OrganizationPath(organization_id), grant_id)


@router.get(WORKSPACE + "/grants", response_model=GrantPage)
async def list_workspace_grants(
    workspace_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> GrantPage:
    path = WorkspacePath(workspace_id)
    return await grants.list_grants(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)


@router.post(WORKSPACE + "/grants", response_model=GrantView, status_code=201)
async def create_workspace_grant(
    workspace_id: str, body: GrantCreate, actor: Actor, runtime: CurrentRuntime
) -> GrantView:
    return await grants.create_grant(runtime.storage, runtime.access, actor, WorkspacePath(workspace_id), body)


@router.patch(WORKSPACE + "/grants/{grant_id}", response_model=GrantView)
async def change_workspace_grant(
    workspace_id: str, grant_id: str, body: GrantUpdate, actor: Actor, runtime: CurrentRuntime
) -> GrantView:
    """The grant is replaced: the result carries its new ID."""
    path = WorkspacePath(workspace_id)
    return await grants.change_role(runtime.storage, runtime.access, actor, path, grant_id, body)


@router.delete(WORKSPACE + "/grants/{grant_id}", status_code=204)
async def delete_workspace_grant(workspace_id: str, grant_id: str, actor: Actor, runtime: CurrentRuntime) -> None:
    await grants.delete_grant(runtime.storage, runtime.access, actor, WorkspacePath(workspace_id), grant_id)


@router.get(ORGANIZATION + "/invitations", response_model=InvitationPage)
async def list_organization_invitations(
    organization_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> InvitationPage:
    path = OrganizationPath(organization_id)
    return await invitations.list_invitations(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)


@router.post(ORGANIZATION + "/invitations", response_model=InvitationReceipt, status_code=201)
async def create_organization_invitation(
    organization_id: str, body: InvitationCreate, actor: Actor, runtime: CurrentRuntime
) -> InvitationReceipt:
    return await invitations.create_invitation(
        runtime.storage, runtime.access, runtime.keys, runtime.settings, actor, OrganizationPath(organization_id), body
    )


@router.post(ORGANIZATION + "/invitations/{invitation_id}/resend", response_model=InvitationReceipt)
async def resend_organization_invitation(
    organization_id: str, invitation_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> InvitationReceipt:
    return await invitations.resend_invitation(
        runtime.storage,
        runtime.access,
        runtime.keys,
        runtime.settings,
        actor,
        OrganizationPath(organization_id),
        invitation_id,
        if_match=if_match,
    )


@router.post(ORGANIZATION + "/invitations/{invitation_id}/revoke", response_model=Invitation)
async def revoke_organization_invitation(
    response: Response,
    organization_id: str,
    invitation_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Invitation:
    path = OrganizationPath(organization_id)
    return tagged(
        response,
        await invitations.revoke_invitation(
            runtime.storage, runtime.access, actor, path, invitation_id, if_match=if_match
        ),
    )


@router.get(WORKSPACE + "/invitations", response_model=InvitationPage)
async def list_workspace_invitations(
    workspace_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> InvitationPage:
    path = WorkspacePath(workspace_id)
    return await invitations.list_invitations(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)


@router.post(WORKSPACE + "/invitations", response_model=InvitationReceipt, status_code=201)
async def create_workspace_invitation(
    workspace_id: str, body: InvitationCreate, actor: Actor, runtime: CurrentRuntime
) -> InvitationReceipt:
    return await invitations.create_invitation(
        runtime.storage, runtime.access, runtime.keys, runtime.settings, actor, WorkspacePath(workspace_id), body
    )


@router.post(WORKSPACE + "/invitations/{invitation_id}/resend", response_model=InvitationReceipt)
async def resend_workspace_invitation(
    workspace_id: str, invitation_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> InvitationReceipt:
    return await invitations.resend_invitation(
        runtime.storage,
        runtime.access,
        runtime.keys,
        runtime.settings,
        actor,
        WorkspacePath(workspace_id),
        invitation_id,
        if_match=if_match,
    )


@router.post(WORKSPACE + "/invitations/{invitation_id}/revoke", response_model=Invitation)
async def revoke_workspace_invitation(
    response: Response,
    workspace_id: str,
    invitation_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Invitation:
    path = WorkspacePath(workspace_id)
    return tagged(
        response,
        await invitations.revoke_invitation(
            runtime.storage, runtime.access, actor, path, invitation_id, if_match=if_match
        ),
    )


@router.get(WORKSPACE + "/service-accounts", response_model=ServiceAccountPage)
async def list_service_accounts(
    workspace_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> ServiceAccountPage:
    return await service_accounts.list_service_accounts(
        runtime.storage, runtime.access, actor, workspace_id, limit=limit, cursor=cursor
    )


@router.post(WORKSPACE + "/service-accounts", response_model=ServiceAccount, status_code=201)
async def create_service_account(
    response: Response, workspace_id: str, body: ServiceAccountCreate, actor: Actor, runtime: CurrentRuntime
) -> ServiceAccount:
    return tagged(
        response,
        await service_accounts.create_service_account(runtime.storage, runtime.access, actor, workspace_id, body),
    )


@router.get(WORKSPACE + "/service-accounts/{account_id}", response_model=ServiceAccount)
async def get_service_account(
    response: Response, workspace_id: str, account_id: str, actor: Actor, runtime: CurrentRuntime
) -> ServiceAccount:
    return tagged(
        response,
        await service_accounts.get_service_account(runtime.storage, runtime.access, actor, workspace_id, account_id),
    )


@router.patch(WORKSPACE + "/service-accounts/{account_id}", response_model=ServiceAccount)
async def update_service_account(
    response: Response,
    workspace_id: str,
    account_id: str,
    body: ServiceAccountUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> ServiceAccount:
    return tagged(
        response,
        await service_accounts.update_service_account(
            runtime.storage, runtime.access, actor, workspace_id, account_id, body, if_match=if_match
        ),
    )


@router.delete(WORKSPACE + "/service-accounts/{account_id}", response_model=ServiceAccount)
async def delete_service_account(
    response: Response,
    workspace_id: str,
    account_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> ServiceAccount:
    return tagged(
        response,
        await service_accounts.delete_service_account(
            runtime.storage, runtime.access, actor, workspace_id, account_id, if_match=if_match
        ),
    )


@router.get(WORKSPACE + "/service-accounts/{account_id}/keys", response_model=ApiKeyPage)
async def list_service_account_keys(
    workspace_id: str,
    account_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ApiKeyPage:
    return await api_keys.list_workspace_keys(
        runtime.storage, runtime.access, actor, workspace_id, principal_id=account_id, limit=limit, cursor=cursor
    )


@router.post(WORKSPACE + "/service-accounts/{account_id}/keys", response_model=IssuedKey, status_code=201)
async def create_service_account_key(
    workspace_id: str, account_id: str, body: KeyCreate, actor: Actor, runtime: CurrentRuntime
) -> IssuedKey:
    """Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation."""
    return await api_keys.create_service_account_key(
        runtime.storage, runtime.access, actor, workspace_id, account_id, body
    )


@router.get(WORKSPACE + "/keys", response_model=ApiKeyPage)
async def list_workspace_keys(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    principal_id: str | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ApiKeyPage:
    return await api_keys.list_workspace_keys(
        runtime.storage, runtime.access, actor, workspace_id, principal_id=principal_id, limit=limit, cursor=cursor
    )


@router.delete(WORKSPACE + "/keys/{key_id}", response_model=ApiKey)
async def revoke_workspace_key(
    response: Response, workspace_id: str, key_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> ApiKey:
    return tagged(
        response,
        await api_keys.revoke_workspace_key(
            runtime.storage, runtime.access, actor, workspace_id, key_id, if_match=if_match
        ),
    )
