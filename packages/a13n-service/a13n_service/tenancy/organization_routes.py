"""Organizations, workspaces, their icons and their audit events over HTTP."""

from fastapi import APIRouter, Response

from a13n_service.infra import images
from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.tenancy import organizations, workspaces
from a13n_service.tenancy.access import OrganizationPath, WorkspacePath
from a13n_service.tenancy.audit import list_audit_events
from a13n_service.tenancy.requests import Actor, CurrentRuntime, ImageBody
from a13n_service.tenancy.schemas import (
    AuditPage,
    Organization,
    OrganizationPage,
    OrganizationUpdate,
    Workspace,
    WorkspaceCreate,
    WorkspacePage,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["tenancy"])


@router.get("/organizations", response_model=OrganizationPage)
async def list_organizations(
    actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> OrganizationPage:
    return await organizations.list_organizations(runtime.storage, actor, limit=limit, cursor=cursor)


@router.get("/organizations/{organization_id}", response_model=Organization)
async def get_organization(
    response: Response, organization_id: str, actor: Actor, runtime: CurrentRuntime
) -> Organization:
    return tagged(response, await organizations.get_organization(runtime.storage, actor, organization_id))


@router.patch("/organizations/{organization_id}", response_model=Organization)
async def update_organization(
    response: Response,
    organization_id: str,
    body: OrganizationUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Organization:
    return tagged(
        response,
        await organizations.update_organization(
            runtime.storage, runtime.access, actor, organization_id, body, if_match=if_match
        ),
    )


@router.put("/organizations/{organization_id}/icon", response_model=Organization, openapi_extra=images.UPLOAD)
async def put_organization_icon(
    response: Response,
    organization_id: str,
    data: ImageBody,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Organization:
    return tagged(
        response,
        await organizations.change_icon(
            runtime.storage, runtime.access, runtime.objects, actor, organization_id, data, if_match=if_match
        ),
    )


@router.delete("/organizations/{organization_id}/icon", response_model=Organization)
async def delete_organization_icon(
    response: Response, organization_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Organization:
    return tagged(
        response,
        await organizations.change_icon(
            runtime.storage, runtime.access, runtime.objects, actor, organization_id, None, if_match=if_match
        ),
    )


@router.get("/organizations/{organization_id}/icon", response_class=Response, responses=images.CONTENT)
async def get_organization_icon(organization_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    image = await organizations.get_icon(runtime.storage, actor, organization_id)
    return await images.serve(runtime.objects, organization_id, image)


@router.get("/organizations/{organization_id}/workspaces", response_model=WorkspacePage)
async def list_organization_workspaces(
    organization_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> WorkspacePage:
    return await workspaces.list_workspaces(
        runtime.storage, actor, organization_id=organization_id, limit=limit, cursor=cursor
    )


@router.post("/organizations/{organization_id}/workspaces", response_model=Workspace, status_code=201)
async def create_workspace(
    response: Response, organization_id: str, body: WorkspaceCreate, actor: Actor, runtime: CurrentRuntime
) -> Workspace:
    return tagged(
        response,
        await workspaces.create_workspace(
            runtime.storage, runtime.access, actor, organization_id, body, on_created=runtime.workspace_created
        ),
    )


@router.get("/organizations/{organization_id}/audit-events", response_model=AuditPage)
async def list_organization_audit_events(
    organization_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> AuditPage:
    path = OrganizationPath(organization_id)
    return await list_audit_events(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)


@router.get("/workspaces", response_model=WorkspacePage)
async def list_workspaces(
    actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> WorkspacePage:
    return await workspaces.list_workspaces(runtime.storage, actor, limit=limit, cursor=cursor)


@router.get("/workspaces/{workspace_id}", response_model=Workspace)
async def get_workspace(response: Response, workspace_id: str, actor: Actor, runtime: CurrentRuntime) -> Workspace:
    return tagged(response, await workspaces.get_workspace(runtime.storage, actor, workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=Workspace)
async def update_workspace(
    response: Response,
    workspace_id: str,
    body: WorkspaceUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Workspace:
    return tagged(
        response,
        await workspaces.update_workspace(
            runtime.storage, runtime.access, actor, workspace_id, body, if_match=if_match
        ),
    )


@router.put("/workspaces/{workspace_id}/icon", response_model=Workspace, openapi_extra=images.UPLOAD)
async def put_workspace_icon(
    response: Response,
    workspace_id: str,
    data: ImageBody,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Workspace:
    return tagged(
        response,
        await workspaces.change_icon(
            runtime.storage, runtime.access, runtime.objects, actor, workspace_id, data, if_match=if_match
        ),
    )


@router.delete("/workspaces/{workspace_id}/icon", response_model=Workspace)
async def delete_workspace_icon(
    response: Response, workspace_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Workspace:
    return tagged(
        response,
        await workspaces.change_icon(
            runtime.storage, runtime.access, runtime.objects, actor, workspace_id, None, if_match=if_match
        ),
    )


@router.get("/workspaces/{workspace_id}/icon", response_class=Response, responses=images.CONTENT)
async def get_workspace_icon(workspace_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    image = await workspaces.get_icon(runtime.storage, actor, workspace_id)
    return await images.serve(runtime.objects, workspace_id, image)


@router.post("/workspaces/{workspace_id}/archive", response_model=Workspace)
async def archive_workspace(
    response: Response, workspace_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Workspace:
    return tagged(
        response,
        await workspaces.archive_workspace(runtime.storage, runtime.access, actor, workspace_id, if_match=if_match),
    )


@router.get("/workspaces/{workspace_id}/audit-events", response_model=AuditPage)
async def list_workspace_audit_events(
    workspace_id: str, actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> AuditPage:
    path = WorkspacePath(workspace_id)
    return await list_audit_events(runtime.storage, runtime.access, actor, path, limit=limit, cursor=cursor)
