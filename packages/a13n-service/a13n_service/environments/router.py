"""Workspace Provider, template, and actual Environment management routes."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey, IfMatch
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.labels import LabelFilterValues, LabelsBody, parse_label_filters
from a13n_service.request_runtime import get_control_runtime

from .domain import (
    CancelDockerImageRequest,
    Collection,
    CreateEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
    CreateTemplateRevisionRequest,
    Environment,
    EnvironmentCommand,
    EnvironmentCommandRequest,
    EnvironmentDetail,
    EnvironmentProvider,
    EnvironmentProviderDefinition,
    EnvironmentTemplate,
    EnvironmentTemplateRevision,
    ReplaceCredentialRequest,
    TestDockerImageRequest,
    UpdateEnvironmentRequest,
    UpdateProviderRequest,
    UpdateTemplateRequest,
)
from .errors import EnvironmentManagementError
from .image_jobs import ImageTestResponse, ProviderConnectivity
from .mount_router import router as mount_router
from .service import EnvironmentService
from .websocket.router import router as client_connection_router

router = APIRouter(prefix="/api/v1", tags=["environments"])
router.include_router(client_connection_router)
router.include_router(mount_router)
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
Limit = Annotated[int, Query(ge=1, le=100)]


def _service(request: Request) -> EnvironmentService:
    control = get_control_runtime(request)
    if control is None:
        raise EnvironmentManagementError(
            "environment_unavailable", "Environment control is unavailable", category=ErrorCategory.unavailable
        )
    return control.environments


@router.get("/environment-provider-types")
async def provider_types(request: Request, actor: Actor) -> Collection[EnvironmentProviderDefinition]:
    return await _service(request).provider_types(actor)


@router.get("/environment-provider-types/{provider_type}")
async def get_provider_type(request: Request, actor: Actor, provider_type: str) -> EnvironmentProviderDefinition:
    catalog = await _service(request).provider_types(actor)
    for item in catalog.items:
        if item.type == provider_type:
            return item
    raise EnvironmentManagementError(
        "environment_provider_type_not_found", "Provider type was not found", category=ErrorCategory.not_found
    )


@router.post("/workspaces/{workspace}/environment-providers", status_code=201)
async def create_provider(
    request: Request, actor: Actor, workspace_id: WorkspaceId, body: CreateProviderRequest
) -> EnvironmentProvider:
    return await _service(request).create_provider(actor=actor, workspace_id=workspace_id, request=body)


@router.patch("/environment-providers/{provider_id}")
async def update_provider(
    request: Request, actor: Actor, provider_id: str, body: UpdateProviderRequest, if_match: IfMatch
) -> EnvironmentProvider:
    return await _service(request).update_provider(
        actor=actor, provider_id=provider_id, request=body, if_match=if_match
    )


@router.put("/environment-providers/{provider_id}/credential")
async def replace_credential(
    request: Request, actor: Actor, provider_id: str, body: ReplaceCredentialRequest, if_match: IfMatch
) -> EnvironmentProvider:
    return await _service(request).replace_credential(
        actor=actor, provider_id=provider_id, request=body, if_match=if_match
    )


@router.get("/environment-providers/{provider_id}/connectivity")
async def provider_connectivity(request: Request, actor: Actor, provider_id: str) -> ProviderConnectivity:
    return await _service(request).provider_connectivity(actor=actor, provider_id=provider_id)


@router.post("/environment-providers/{provider_id}/test-image")
async def test_image(
    request: Request, actor: Actor, provider_id: str, body: TestDockerImageRequest
) -> ImageTestResponse:
    operation = asyncio.create_task(
        _service(request).test_docker_image(
            actor=actor,
            provider_id=provider_id,
            workspace_id=body.workspace_id,
            request_id=body.request_id,
            configuration=body.configuration,
        )
    )
    try:
        while not operation.done():
            if await request.is_disconnected():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                raise HTTPException(status_code=499, detail="Image test client disconnected")
            await asyncio.sleep(0.2)
        return await operation
    finally:
        if not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@router.post("/environment-providers/{provider_id}/test-image/{request_id}/cancel", status_code=204)
async def cancel_image_test(
    request: Request,
    actor: Actor,
    provider_id: str,
    request_id: Annotated[str, Path(pattern=r"^envtest_[0-9a-f]{32}$")],
    body: CancelDockerImageRequest,
) -> None:
    await _service(request).cancel_docker_image(
        actor=actor, provider_id=provider_id, workspace_id=body.workspace_id, request_id=request_id
    )


@router.post("/workspaces/{workspace}/environment-templates", status_code=201)
async def create_template(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateTemplateRequest,
    idempotency_key: IdempotencyKey,
) -> EnvironmentTemplate:
    return await _service(request).create_template(
        actor=actor, workspace_id=workspace_id, request=body, idempotency_key=idempotency_key
    )


@router.patch("/environment-templates/{template_id}")
async def update_template(
    request: Request, actor: Actor, template_id: str, body: UpdateTemplateRequest, if_match: IfMatch
) -> EnvironmentTemplate:
    return await _service(request).update_template(
        actor=actor, template_id=template_id, request=body, if_match=if_match
    )


@router.post("/environment-templates/{template_id}/revisions", status_code=201)
async def create_revision(
    request: Request, actor: Actor, template_id: str, body: CreateTemplateRevisionRequest
) -> EnvironmentTemplateRevision:
    return await _service(request).create_revision(actor=actor, template_id=template_id, request=body)


@router.get("/environment-templates/{template_id}/revisions")
async def list_revisions(
    request: Request, actor: Actor, template_id: str, limit: Limit = 50, cursor: str | None = None
) -> Collection[EnvironmentTemplateRevision]:
    return await _service(request).list_revisions(actor=actor, template_id=template_id, limit=limit, cursor=cursor)


@router.get("/environment-template-revisions/{revision_id}")
async def get_revision(request: Request, actor: Actor, revision_id: str) -> EnvironmentTemplateRevision:
    return await _service(request).get_revision(actor=actor, revision_id=revision_id)


@router.post("/workspaces/{workspace}/environments", status_code=201)
async def create_environment(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateEnvironmentRequest,
    idempotency_key: IdempotencyKey,
) -> Environment:
    return await _service(request).create_environment(
        actor=actor, workspace_id=workspace_id, request=body, idempotency_key=idempotency_key
    )


@router.get("/workspaces/{workspace}/environment-providers")
async def list_providers(
    request: Request, actor: Actor, workspace_id: WorkspaceId, limit: Limit = 50, cursor: str | None = None
) -> Collection[EnvironmentProvider]:
    return await _service(request).list_providers(actor=actor, workspace_id=workspace_id, limit=limit, cursor=cursor)


@router.get("/environment-providers/{resource_id}")
async def get_provider(request: Request, response: Response, actor: Actor, resource_id: str) -> EnvironmentProvider:
    resource = await _service(request).get_provider(actor=actor, resource_id=resource_id)
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.get("/workspaces/{workspace}/environment-templates")
async def list_templates(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Limit = 50,
    cursor: str | None = None,
    label: Annotated[LabelFilterValues, Query()] = (),
) -> Collection[EnvironmentTemplate]:
    return await _service(request).list_templates(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        labels=parse_label_filters(label),
    )


@router.get("/environment-templates/{resource_id}")
async def get_template(request: Request, response: Response, actor: Actor, resource_id: str) -> EnvironmentTemplate:
    resource = await _service(request).get_template(actor=actor, resource_id=resource_id)
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.get("/environment-templates/{template_id}/labels", response_model=LabelsBody)
async def get_template_labels(request: Request, response: Response, actor: Actor, template_id: str) -> LabelsBody:
    body, etag = await _service(request).get_template_labels(actor=actor, template_id=template_id)
    response.headers["ETag"] = etag
    return body


@router.put("/environment-templates/{template_id}/labels", response_model=LabelsBody)
async def put_template_labels(
    request: Request,
    response: Response,
    actor: Actor,
    template_id: str,
    body: LabelsBody,
    if_match: IfMatch,
) -> LabelsBody:
    result, etag = await _service(request).replace_template_labels(
        actor=actor, template_id=template_id, body=body, if_match=if_match
    )
    response.headers["ETag"] = etag
    return result


@router.get("/workspaces/{workspace}/environments")
async def list_environments(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Limit = 50,
    cursor: str | None = None,
    label: Annotated[LabelFilterValues, Query()] = (),
) -> Collection[Environment]:
    return await _service(request).list_environments(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        labels=parse_label_filters(label),
    )


@router.get("/environments/{resource_id}")
async def get_environment(request: Request, response: Response, actor: Actor, resource_id: str) -> EnvironmentDetail:
    resource = await _service(request).get_environment(actor=actor, resource_id=resource_id)
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.get("/environments/{environment_id}/labels", response_model=LabelsBody)
async def get_environment_labels(request: Request, response: Response, actor: Actor, environment_id: str) -> LabelsBody:
    body, etag = await _service(request).get_environment_labels(actor=actor, environment_id=environment_id)
    response.headers["ETag"] = etag
    return body


@router.put("/environments/{environment_id}/labels", response_model=LabelsBody)
async def put_environment_labels(
    request: Request,
    response: Response,
    actor: Actor,
    environment_id: str,
    body: LabelsBody,
    if_match: IfMatch,
) -> LabelsBody:
    result, etag = await _service(request).replace_environment_labels(
        actor=actor, environment_id=environment_id, body=body, if_match=if_match
    )
    response.headers["ETag"] = etag
    return result


@router.patch("/environments/{environment_id}")
async def update_environment(
    request: Request,
    response: Response,
    actor: Actor,
    environment_id: str,
    body: UpdateEnvironmentRequest,
    if_match: IfMatch,
) -> Environment:
    resource = await _service(request).update_environment(
        actor=actor, environment_id=environment_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)
    return resource


@router.post("/environments/{environment_id}/stop", status_code=202)
async def stop_environment(
    request: Request, actor: Actor, environment_id: str, idempotency_key: IdempotencyKey
) -> EnvironmentCommand:
    return await _service(request).request_command(
        actor=actor,
        environment_id=environment_id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key=idempotency_key,
    )


@router.post("/environments/{environment_id}/delete", status_code=202)
async def delete_environment(
    request: Request, actor: Actor, environment_id: str, idempotency_key: IdempotencyKey
) -> EnvironmentCommand:
    return await _service(request).request_command(
        actor=actor,
        environment_id=environment_id,
        request=EnvironmentCommandRequest(action="delete"),
        idempotency_key=idempotency_key,
    )


@router.get("/environment-commands/{command_id}")
async def get_command(request: Request, actor: Actor, command_id: str) -> EnvironmentCommand:
    return await _service(request).get_command(actor=actor, command_id=command_id)


@router.post("/organizations/{organization}/environment-providers", status_code=201)
async def organization_create_provider(
    request: Request, actor: Actor, organization_id: OrganizationId, body: CreateProviderRequest
) -> EnvironmentProvider:
    require_organization_boundary(actor, organization_id)
    return await _service(request).create_provider(actor=actor, workspace_id=None, request=body)


@router.post("/organizations/{organization}/environment-templates", status_code=201)
async def organization_create_template(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    body: CreateTemplateRequest,
    idempotency_key: IdempotencyKey,
) -> EnvironmentTemplate:
    require_organization_boundary(actor, organization_id)
    return await _service(request).create_template(
        actor=actor, workspace_id=None, request=body, idempotency_key=idempotency_key
    )


@router.get("/organizations/{organization}/environment-providers")
async def organization_list_providers(
    request: Request, actor: Actor, organization_id: OrganizationId, limit: Limit = 50, cursor: str | None = None
) -> Collection[EnvironmentProvider]:
    require_organization_boundary(actor, organization_id)
    return await _service(request).list_providers(actor=actor, workspace_id=None, limit=limit, cursor=cursor)


@router.get("/organizations/{organization}/environment-templates")
async def organization_list_templates(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    limit: Limit = 50,
    cursor: str | None = None,
    label: Annotated[LabelFilterValues, Query()] = (),
) -> Collection[EnvironmentTemplate]:
    require_organization_boundary(actor, organization_id)
    return await _service(request).list_templates(
        actor=actor,
        workspace_id=None,
        limit=limit,
        cursor=cursor,
        labels=parse_label_filters(label),
    )
