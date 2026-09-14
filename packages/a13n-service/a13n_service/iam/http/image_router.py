"""Bounded binary image input and authenticated content reads."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_images import IMAGE_DOWNLOAD, IMAGE_UPLOAD, image_body, image_response
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.request_runtime import get_process_runtime

from ..management.images import ImageOwner
from ..schemas import Organization, User, Workspace
from ..service_common import identity_error
from .dependencies import Actor, identity, private_response

router = APIRouter(prefix="/api/v1", tags=["identity-images"], dependencies=[Depends(private_response)])


IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def objects(request: Request):
    runtime = get_process_runtime(request)
    if runtime is None:
        raise identity_error("identity_unavailable", "Identity is unavailable.", ErrorCategory.unavailable)
    return runtime.shared.storage.objects


async def change(
    request: Request, response: Response, actor: Actor, kind: ImageOwner, owner_id: str, if_match: str, *, remove: bool
):
    result = await identity(request).images.replace(
        actor, kind, owner_id, None if remove else await image_body(request), if_match, objects(request)
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


async def read(request: Request, actor: Actor, kind: ImageOwner, owner_id: str, image_id: str) -> Response:
    content = await identity(request).images.read(actor, kind, owner_id, image_id, objects(request))
    return image_response(content)


@router.put("/users/me/avatar", response_model=User, openapi_extra=IMAGE_UPLOAD)
async def put_avatar(request: Request, response: Response, actor: Actor, if_match: IfMatch):
    return await change(request, response, actor, ImageOwner.user, actor.principal.principal_id, if_match, remove=False)


@router.delete("/users/me/avatar", response_model=User)
async def delete_avatar(request: Request, response: Response, actor: Actor, if_match: IfMatch):
    return await change(request, response, actor, ImageOwner.user, actor.principal.principal_id, if_match, remove=True)


@router.get("/users/{user_id}/avatar/{image_id}", response_class=Response, responses=IMAGE_DOWNLOAD)
async def get_avatar(request: Request, actor: Actor, user_id: str, image_id: str) -> Response:
    return await read(request, actor, ImageOwner.user, user_id, image_id)


@router.put("/organizations/{organization}/icon", response_model=Organization, openapi_extra=IMAGE_UPLOAD)
async def put_organization_icon(
    request: Request, response: Response, actor: Actor, organization_id: OrganizationId, if_match: IfMatch
):
    return await change(request, response, actor, ImageOwner.organization, organization_id, if_match, remove=False)


@router.delete("/organizations/{organization}/icon", response_model=Organization)
async def delete_organization_icon(
    request: Request, response: Response, actor: Actor, organization_id: OrganizationId, if_match: IfMatch
):
    return await change(request, response, actor, ImageOwner.organization, organization_id, if_match, remove=True)


@router.get("/organizations/{organization}/icon/{image_id}", response_class=Response, responses=IMAGE_DOWNLOAD)
async def get_organization_icon(
    request: Request, actor: Actor, organization_id: OrganizationId, image_id: str
) -> Response:
    return await read(request, actor, ImageOwner.organization, organization_id, image_id)


@router.put("/workspaces/{workspace}/icon", response_model=Workspace, openapi_extra=IMAGE_UPLOAD)
async def put_workspace_icon(
    request: Request, response: Response, actor: Actor, workspace_id: WorkspaceId, if_match: IfMatch
):
    return await change(request, response, actor, ImageOwner.workspace, workspace_id, if_match, remove=False)


@router.delete("/workspaces/{workspace}/icon", response_model=Workspace)
async def delete_workspace_icon(
    request: Request, response: Response, actor: Actor, workspace_id: WorkspaceId, if_match: IfMatch
):
    return await change(request, response, actor, ImageOwner.workspace, workspace_id, if_match, remove=True)


@router.get("/workspaces/{workspace}/icon/{image_id}", response_class=Response, responses=IMAGE_DOWNLOAD)
async def get_workspace_icon(request: Request, actor: Actor, workspace_id: WorkspaceId, image_id: str) -> Response:
    return await read(request, actor, ImageOwner.workspace, workspace_id, image_id)
