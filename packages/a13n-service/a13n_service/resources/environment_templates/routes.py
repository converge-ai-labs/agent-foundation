"""Workspace environment templates: CRUD with ETags; `enabled: false` retires one instead of deletion."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.resources.environment_templates import service
from a13n_service.resources.environment_templates.schemas import Template, TemplateCreate, TemplatePage, TemplateUpdate
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/environment-templates", tags=["environments"])


@router.post("", response_model=Template, status_code=201, openapi_extra={"x-a13n-mcp": True})
async def create_template(
    response: Response, workspace_id: WorkspaceId, body: TemplateCreate, actor: Actor, runtime: CurrentRuntime
) -> Template:
    result = await service.create_template(runtime.storage, actor, workspace_id, body, registry=runtime.registry)
    return tagged(response, result)


@router.get("", response_model=TemplatePage, openapi_extra={"x-a13n-mcp": True})
async def list_templates(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> TemplatePage:
    return await service.list_templates(
        runtime.storage, actor, workspace_id, labels=label or [], limit=limit, cursor=cursor
    )


@router.get("/{template_id}", response_model=Template, openapi_extra={"x-a13n-mcp": True})
async def get_template(
    response: Response, workspace_id: WorkspaceId, template_id: str, actor: Actor, runtime: CurrentRuntime
) -> Template:
    return tagged(response, await service.get_template(runtime.storage, actor, workspace_id, template_id))


@router.patch("/{template_id}", response_model=Template, openapi_extra={"x-a13n-mcp": True})
async def update_template(
    response: Response,
    workspace_id: WorkspaceId,
    template_id: str,
    body: TemplateUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Template:
    result = await service.update_template(
        runtime.storage, actor, workspace_id, template_id, body, if_match=if_match, registry=runtime.registry
    )
    return tagged(response, result)
