"""Connector public `/api/v1` management routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authenticate_request,
    authorize_workspace,
)
from a13n_service.storage import short_session

from .domain import (
    Connector,
    ConnectorCreateResult,
    ConnectorRevision,
    ConnectorRevisionCreateResult,
    CreateConnector,
    CreateConnectorRevision,
    Description,
    Name,
    ProviderKey,
    ProviderVersion,
    UpdateConnector,
)
from .errors import ConnectorError
from .operations import ConnectorProviderOperations
from .service import ConnectorService

router = APIRouter(prefix="/api/v1", tags=["connectors"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


class ConnectorProviderCapabilitiesResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: bool
    connections: bool
    events: bool
    event_delivery: str | None


class ConnectorProviderResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str
    description: str
    contract_version: str
    provider_config_schemas: dict[str, dict[str, JsonValue]]
    capabilities: ConnectorProviderCapabilitiesResource
    connection_setup_modes: tuple[str, ...]


class ConnectorProviderCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ConnectorProviderResource, ...]
    next_cursor: None = None


class ConnectorCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[Connector, ...]
    next_cursor: None = None


class ConnectorRevisionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ConnectorRevision, ...]
    next_cursor: None = None


class ConnectorCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    enabled: bool = True
    provider_key: ProviderKey
    provider_config_version: ProviderVersion
    config: dict[str, JsonValue] = Field(default_factory=dict)


class ConnectorRevisionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: ProviderKey
    provider_config_version: ProviderVersion
    config: dict[str, JsonValue] = Field(default_factory=dict)


def _service(request: Request) -> ConnectorService:
    service: ConnectorService | None = getattr(request.app.state, "connector_service", None)
    if service is None:
        raise ConnectorError("Connector management is unavailable.", code="dependency_unavailable")
    return service


def _providers(request: Request) -> ConnectorProviderOperations:
    providers: ConnectorProviderOperations | None = getattr(request.app.state, "connector_provider_operations", None)
    if providers is None:
        raise ConnectorError("Connector Provider catalog is unavailable.", code="dependency_unavailable")
    return providers


async def _authorize(
    request: Request,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
) -> AuthorizedWorkspace:
    sessions = request.app.state.db_session_factory
    try:
        async with short_session(sessions) as session:
            return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        code = "not_found" if error.concealed else "permission_denied"
        raise ConnectorError("Connector resource is unavailable.", code=code) from error


async def _provider_resource(key: str, providers: ConnectorProviderOperations) -> ConnectorProviderResource:
    metadata = await providers.metadata(key)
    return ConnectorProviderResource(
        key=key,
        display_name=metadata.display_name,
        description=metadata.description,
        contract_version=metadata.contract_version,
        provider_config_schemas={version: dict(schema) for version, schema in metadata.provider_config_schemas.items()},
        capabilities=ConnectorProviderCapabilitiesResource(
            tools=metadata.capabilities.tools,
            connections=metadata.capabilities.connections,
            events=metadata.capabilities.events,
            event_delivery=metadata.capabilities.event_delivery,
        ),
        connection_setup_modes=metadata.connection_setup_modes,
    )


@router.get("/connector-providers", response_model=ConnectorProviderCollection)
async def list_connector_providers(request: Request, actor: Actor) -> ConnectorProviderCollection:
    await _authorize(request, actor, actor.boundary_workspace_id, WorkspaceAction.connector_read)
    providers = _providers(request)
    registrations = await providers.registrations()
    items = [await _provider_resource(item.provider_key, providers) for item in registrations]
    return ConnectorProviderCollection(items=tuple(items))


@router.get("/connector-providers/{provider_key}", response_model=ConnectorProviderResource)
async def get_connector_provider(request: Request, actor: Actor, provider_key: str) -> ConnectorProviderResource:
    await _authorize(request, actor, actor.boundary_workspace_id, WorkspaceAction.connector_read)
    return await _provider_resource(provider_key, _providers(request))


@router.get("/workspaces/{workspace_id}/connectors", response_model=ConnectorCollection)
async def list_connectors(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ConnectorCollection:
    workspace = await _authorize(request, actor, workspace_id, WorkspaceAction.connector_read)
    items = await _service(request).list(workspace.organization_id, workspace.workspace_id, limit=limit)
    return ConnectorCollection(items=items)


@router.post(
    "/workspaces/{workspace_id}/connectors",
    response_model=ConnectorCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector(
    request: Request,
    actor: Actor,
    workspace_id: str,
    body: ConnectorCreateBody,
) -> ConnectorCreateResult:
    workspace = await _authorize(request, actor, workspace_id, WorkspaceAction.connector_create)
    return await _service(request).create(
        CreateConnector(
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            created_by=actor.principal,
            **body.model_dump(),
        )
    )


@router.get("/connectors/{connector_id}", response_model=Connector)
async def get_connector(request: Request, actor: Actor, connector_id: str) -> Connector:
    workspace = await _authorize(
        request,
        actor,
        actor.boundary_workspace_id,
        WorkspaceAction.connector_read,
    )
    return await _service(request).get(workspace.organization_id, workspace.workspace_id, connector_id)


@router.patch("/connectors/{connector_id}", response_model=Connector)
async def patch_connector(
    request: Request,
    actor: Actor,
    connector_id: str,
    body: UpdateConnector,
) -> Connector:
    workspace = await _authorize(
        request,
        actor,
        actor.boundary_workspace_id,
        WorkspaceAction.connector_configure,
    )
    return await _service(request).update(
        workspace.organization_id,
        workspace.workspace_id,
        connector_id,
        body,
    )


@router.get("/connectors/{connector_id}/revisions", response_model=ConnectorRevisionCollection)
async def list_connector_revisions(
    request: Request,
    actor: Actor,
    connector_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ConnectorRevisionCollection:
    workspace = await _authorize(
        request,
        actor,
        actor.boundary_workspace_id,
        WorkspaceAction.connector_read,
    )
    items = await _service(request).list_revisions(
        workspace.organization_id,
        workspace.workspace_id,
        connector_id,
        limit=limit,
    )
    return ConnectorRevisionCollection(items=items)


@router.post(
    "/connectors/{connector_id}/revisions",
    response_model=ConnectorRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector_revision(
    request: Request,
    actor: Actor,
    connector_id: str,
    body: ConnectorRevisionCreateBody,
    response: Response,
) -> ConnectorRevisionCreateResult:
    workspace = await _authorize(
        request,
        actor,
        actor.boundary_workspace_id,
        WorkspaceAction.connector_configure,
    )
    result = await _service(request).create_revision(
        workspace.organization_id,
        workspace.workspace_id,
        connector_id,
        CreateConnectorRevision(created_by=actor.principal, **body.model_dump()),
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/connector-revisions/{connector_revision_id}", response_model=ConnectorRevision)
async def get_connector_revision(
    request: Request,
    actor: Actor,
    connector_revision_id: str,
) -> ConnectorRevision:
    workspace = await _authorize(
        request,
        actor,
        actor.boundary_workspace_id,
        WorkspaceAction.connector_read,
    )
    return await _service(request).get_revision(
        workspace.organization_id,
        workspace.workspace_id,
        connector_revision_id,
    )
