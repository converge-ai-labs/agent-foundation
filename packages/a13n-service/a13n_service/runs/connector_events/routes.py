"""Authenticated event management and the exact signature-authenticated callback route."""

from typing import Literal

from a13n_harness.providers.connector.events import EventType
from fastapi import APIRouter, Request, Response

from a13n_service.infra.http import IdempotencyKey, IfMatch, PageLimit, tagged
from a13n_service.infra.redis import rate_limit
from a13n_service.resources.connector_providers import events, webhooks
from a13n_service.runs.connector_events import ingress, service
from a13n_service.runs.connector_events.schemas import ConnectorEvent as Event
from a13n_service.runs.connector_events.schemas import EventPage
from a13n_service.runs.connector_events.triggers import service as triggers
from a13n_service.runs.connector_events.triggers.schemas import (
    ConnectorTrigger as Trigger,
)
from a13n_service.runs.connector_events.triggers.schemas import (
    EventTypePage,
    TriggerCreate,
    TriggerPage,
    TriggerUpdate,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["connector events"])
READ = {"x-a13n-mcp": True}


@router.get(
    "/connector-providers/{provider_id}/apps/{app}/event-types", response_model=EventTypePage, openapi_extra=READ
)
async def list_connector_event_types(
    provider_id: str, app: str, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime, refresh: bool = False
) -> EventTypePage:
    return EventTypePage(
        items=await events.definitions(runtime, actor, workspace_id, provider_id, app, refresh=refresh)
    )


@router.get(
    "/connector-providers/{provider_id}/apps/{app}/event-types/{event_type}",
    response_model=EventType,
    openapi_extra=READ,
)
async def get_connector_event_type(
    provider_id: str, app: str, event_type: str, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime
) -> EventType:
    return (await events.definitions(runtime, actor, workspace_id, provider_id, app, key=event_type))[0]


@router.get("/connector-providers/{provider_id}/event-webhook", response_model=webhooks.Webhook, openapi_extra=READ)
async def get_connector_event_webhook(
    provider_id: str, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime
) -> webhooks.Webhook:
    return await webhooks.get(runtime, actor, workspace_id, provider_id)


@router.post("/connector-providers/{provider_id}/event-webhook", response_model=webhooks.Webhook)
async def configure_connector_event_webhook(
    provider_id: str,
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
    rotate: bool = False,
) -> webhooks.Webhook:
    return await webhooks.configure(runtime, actor, workspace_id, provider_id, if_match=if_match, rotate=rotate)


@router.get("/connector-triggers", response_model=TriggerPage, openapi_extra=READ)
async def list_connector_triggers(
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    connection_id: str | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> TriggerPage:
    return await triggers.list_triggers(
        runtime, actor, workspace_id, connection_id=connection_id, limit=limit, cursor=cursor
    )


@router.post("/connector-triggers", response_model=Trigger)
async def create_connector_trigger(
    body: TriggerCreate,
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> Trigger:
    result, created = await triggers.create(runtime, actor, workspace_id, body, request_key=idempotency_key)
    response.status_code = 201 if created else 200
    return tagged(response, result)


@router.get("/connector-triggers/{trigger_id}", response_model=Trigger, openapi_extra=READ)
async def get_connector_trigger(
    trigger_id: str, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime, response: Response
) -> Trigger:
    return tagged(response, await triggers.get(runtime, actor, workspace_id, trigger_id))


@router.patch("/connector-triggers/{trigger_id}", response_model=Trigger)
async def update_connector_trigger(
    trigger_id: str,
    body: TriggerUpdate,
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    response: Response,
    if_match: IfMatch = None,
) -> Trigger:
    return tagged(response, await triggers.update(runtime, actor, workspace_id, trigger_id, body, if_match=if_match))


@router.post("/connector-triggers/{trigger_id}/{command}", response_model=Trigger)
async def transition_connector_trigger(
    trigger_id: str,
    command: Literal["enable", "pause", "retire", "reconcile"],
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    response: Response,
    if_match: IfMatch = None,
) -> Trigger:
    if command == "reconcile":
        return tagged(response, await triggers.reconcile(runtime, actor, workspace_id, trigger_id, if_match=if_match))
    state = {"enable": "enabled", "pause": "paused", "retire": "retired"}[command]
    return tagged(
        response, await triggers.transition(runtime, actor, workspace_id, trigger_id, state, if_match=if_match)
    )


@router.get("/connector-events", response_model=EventPage, openapi_extra=READ)
async def list_connector_events(
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    trigger_id: str | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> EventPage:
    return await service.list_events(runtime, actor, workspace_id, trigger_id=trigger_id, limit=limit, cursor=cursor)


@router.get("/connector-events/{event_id}", response_model=Event, openapi_extra=READ)
async def get_connector_event(
    event_id: str, actor: Actor, workspace_id: WorkspaceId, runtime: CurrentRuntime, response: Response
) -> Event:
    return tagged(response, await service.get(runtime, actor, workspace_id, event_id))


@router.post("/connector-events/{event_id}/retry", response_model=Event)
async def retry_connector_event(
    event_id: str,
    actor: Actor,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    response: Response,
    if_match: IfMatch = None,
) -> Event:
    return tagged(response, await service.retry(runtime, actor, workspace_id, event_id, if_match=if_match))


@router.post("/connector-webhooks/{provider_id}", status_code=204)
async def receive_connector_webhook(provider_id: str, request: Request, runtime: CurrentRuntime) -> Response:
    """Authenticate the sender's signature; receipt confirms durable storage, not Agent completion."""
    peer = request.client.host if request.client else "unknown"
    await rate_limit(runtime.redis, f"connector_webhook:{provider_id}:{peer}", limit=600, window_seconds=60)
    await ingress.receive(runtime, provider_id, await request.body(), request.headers)
    return Response(status_code=204)
