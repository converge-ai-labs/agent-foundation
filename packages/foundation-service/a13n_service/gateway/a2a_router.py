"""A2A 1.0 HTTP+JSON transport routes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, cast

from a2a.types import a2a_pb2 as a2a
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from google.protobuf.json_format import MessageToDict, Parse, ParseError

from a13n_service.iam import AuthenticatedActor, AuthenticationError, authenticate_request
from a13n_service.request_runtime import get_control_runtime

from .a2a import A2AError, A2AService


class _A2ARoute(APIRoute):
    """Keep dependency and parameter failures inside the A2A wire contract."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def a2a_route_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except AuthenticationError:
                return _error(
                    A2AError(
                        "authentication_required",
                        "Authentication is required.",
                        status_code=401,
                    )
                )
            except RequestValidationError:
                return _error(A2AError("invalid_request", "The A2A request is invalid.", status_code=400))
            except A2AError as error:
                return _error(error)

        return a2a_route_handler


router = APIRouter(tags=["a2a"], route_class=_A2ARoute)
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
_VERSION = Annotated[str | None, Header(alias="A2A-Version")]
_EXTENSIONS = Annotated[str | None, Header(alias="A2A-Extensions")]


def _service(request: Request) -> A2AService:
    control = get_control_runtime(request)
    service = None if control is None else control.gateway.a2a
    if service is None:
        raise A2AError("a2a_disabled", "A2A is not enabled for this deployment.", status_code=404)
    return service


def _base_url(request: Request) -> str:
    configured = request.app.state.settings.a2a_public_origin
    return configured or str(request.base_url).rstrip("/")


@router.get("/.well-known/agent-card.json")
async def default_agent_card(request: Request) -> Response:
    agent_id = request.app.state.settings.a2a_default_agent_id
    if agent_id is None:
        return _error(A2AError("agent_not_found", "No default A2A Agent is configured.", status_code=404))
    return await direct_agent_card(request, agent_id)


@router.get("/a2a/v1/agents/{agent_id}/agent-card.json")
async def direct_agent_card(request: Request, agent_id: str) -> Response:
    try:
        card = await _service(request).public_agent_card(agent_id=agent_id, base_url=_base_url(request))
        return _public_card(card, if_none_match=request.headers.get("if-none-match"))
    except A2AError as error:
        return _error(error)


@router.get("/a2a/v1/agents/{agent_id}/extendedAgentCard")
async def extended_agent_card(request: Request, actor: Actor, agent_id: str, version: _VERSION = None) -> Response:
    try:
        _validate_wire(version=version, extensions=None, content_type=None, body_required=False)
        card = await _service(request).extended_agent_card(
            actor=actor,
            agent_id=agent_id,
            base_url=_base_url(request),
        )
        return _json(card, cache_control="private, no-store")
    except A2AError as error:
        return _error(error)


@router.post("/a2a/v1/agents/{agent_id}/message:send")
async def send_message(
    request: Request,
    actor: Actor,
    agent_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
) -> Response:
    try:
        params = await _send_request(request, version=version, extensions=extensions)
        task = await _service(request).send(actor=actor, agent_id=agent_id, request=params)
        if not params.configuration.return_immediately:
            task = await _service(request).wait_task(
                actor=actor,
                agent_id=agent_id,
                task_id=task.id,
                history_length=_configured_history_length(params.configuration),
            )
        return _json(a2a.SendMessageResponse(task=task))
    except (A2AError, ParseError) as error:
        return _error(error)


@router.post("/a2a/v1/agents/{agent_id}/message:stream")
async def stream_message(
    request: Request,
    actor: Actor,
    agent_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
    accept: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        _require_sse(accept)
        params = await _send_request(request, version=version, extensions=extensions)
        task = await _service(request).send(actor=actor, agent_id=agent_id, request=params)
        events = _service(request).stream_task(
            actor=actor,
            agent_id=agent_id,
            task_id=task.id,
            history_length=_configured_history_length(params.configuration),
        )
        return _stream(events)
    except (A2AError, ParseError) as error:
        return _error(error)


@router.get("/a2a/v1/agents/{agent_id}/tasks/{task_id}")
async def get_task(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
    history_length: Annotated[str | None, Query(alias="historyLength", max_length=10)] = None,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        task = await _service(request).get_task(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            history_length=_bounded_int(history_length, name="historyLength", minimum=0),
        )
        return _json(task)
    except A2AError as error:
        return _error(error)


@router.get("/a2a/v1/agents/{agent_id}/tasks")
async def list_tasks(
    request: Request,
    actor: Actor,
    agent_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
    context_id: Annotated[str | None, Query(alias="contextId", max_length=512)] = None,
    status: Annotated[str | None, Query(max_length=64)] = None,
    page_size: Annotated[str | None, Query(alias="pageSize", max_length=10)] = None,
    page_token: Annotated[str | None, Query(alias="pageToken", max_length=2048)] = None,
    history_length: Annotated[str | None, Query(alias="historyLength", max_length=10)] = None,
    status_timestamp_after: Annotated[str | None, Query(alias="statusTimestampAfter", max_length=64)] = None,
    include_artifacts: Annotated[str | None, Query(alias="includeArtifacts", max_length=5)] = None,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        selected_page_size = _bounded_int(page_size, name="pageSize", minimum=1, maximum=100, default=50)
        assert selected_page_size is not None
        page = await _service(request).list_tasks(
            actor=actor,
            agent_id=agent_id,
            context_id=context_id,
            status=_task_state(status),
            page_size=selected_page_size,
            page_token=page_token,
            history_length=_bounded_int(history_length, name="historyLength", minimum=0),
            status_timestamp_after=_timestamp(status_timestamp_after),
            include_artifacts=_boolean(include_artifacts, name="includeArtifacts", default=False),
        )
        response = a2a.ListTasksResponse(
            tasks=page.tasks,
            next_page_token=page.next_page_token or "",
            page_size=selected_page_size,
            total_size=page.total_size,
        )
        payload = MessageToDict(response, preserving_proto_field_name=False)
        payload["nextPageToken"] = page.next_page_token or ""
        return JSONResponse(payload, media_type="application/a2a+json")
    except A2AError as error:
        return _error(error)


@router.post("/a2a/v1/agents/{agent_id}/tasks/{task_id}:cancel")
async def cancel_task(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
) -> Response:
    try:
        _validate_wire(
            version=version,
            extensions=extensions,
            content_type=None,
            body_required=False,
        )
        task = await _service(request).cancel_task(actor=actor, agent_id=agent_id, task_id=task_id)
        return _json(task)
    except A2AError as error:
        return _error(error)


@router.post("/a2a/v1/agents/{agent_id}/tasks/{task_id}:subscribe")
async def subscribe_task(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
    accept: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        _require_sse(accept)
        _validate_wire(
            version=version,
            extensions=extensions,
            content_type=None,
            body_required=False,
        )
        current = await _service(request).get_task(actor=actor, agent_id=agent_id, task_id=task_id)
        if current.status.state in {
            a2a.TASK_STATE_COMPLETED,
            a2a.TASK_STATE_FAILED,
            a2a.TASK_STATE_CANCELED,
        }:
            raise A2AError("task_not_subscribable", "A terminal Task cannot be subscribed.", status_code=409)
        return _stream(_service(request).stream_task(actor=actor, agent_id=agent_id, task_id=task_id))
    except A2AError as error:
        return _error(error)


@router.post("/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs")
async def create_push_configuration(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
) -> Response:
    try:
        _validate_wire(
            version=version,
            extensions=extensions,
            content_type=request.headers.get("content-type"),
            body_required=True,
        )
        requested = a2a.TaskPushNotificationConfig()
        Parse(await request.body(), requested)
        created = await _service(request).create_push_configuration(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            requested=requested,
        )
        return _json(created)
    except (A2AError, ParseError) as error:
        return _error(error)


@router.get("/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs/{config_id}")
async def get_push_configuration(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    config_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        selected = await _service(request).get_push_configuration(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            config_id=config_id,
        )
        return _json(selected)
    except A2AError as error:
        return _error(error)


@router.get("/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs")
async def list_push_configurations(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 50,
    page_token: Annotated[str | None, Query(alias="pageToken", max_length=2048)] = None,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        configs, next_page_token = await _service(request).list_push_configurations(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            page_size=page_size,
            page_token=page_token,
        )
        return _json(
            a2a.ListTaskPushNotificationConfigsResponse(
                configs=configs,
                next_page_token=next_page_token or "",
            )
        )
    except A2AError as error:
        return _error(error)


@router.delete("/a2a/v1/agents/{agent_id}/tasks/{task_id}/pushNotificationConfigs/{config_id}")
async def delete_push_configuration(
    request: Request,
    actor: Actor,
    agent_id: str,
    task_id: str,
    config_id: str,
    version: _VERSION = None,
    extensions: _EXTENSIONS = None,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        await _service(request).delete_push_configuration(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            config_id=config_id,
        )
        return JSONResponse({}, media_type="application/a2a+json")
    except A2AError as error:
        return _error(error)


async def _send_request(request: Request, *, version: str | None, extensions: str | None) -> a2a.SendMessageRequest:
    _validate_wire(
        version=version,
        extensions=extensions,
        content_type=request.headers.get("content-type"),
        body_required=True,
    )
    params = a2a.SendMessageRequest()
    Parse(await request.body(), params)
    return params


def _validate_wire(
    *,
    version: str | None,
    extensions: str | None,
    content_type: str | None,
    body_required: bool,
) -> None:
    if version != "1.0":
        raise A2AError("version_not_supported", "A2A-Version must be 1.0.", status_code=400)
    if extensions and extensions.strip():
        raise A2AError("extension_not_supported", "A2A extensions are not supported.", status_code=400)
    if body_required and (content_type or "").partition(";")[0].strip().lower() != "application/a2a+json":
        raise A2AError(
            "content_type_not_supported",
            "Content-Type must be application/a2a+json.",
            status_code=415,
        )


def _require_sse(accept: str | None) -> None:
    media_types = {item.partition(";")[0].strip().lower() for item in (accept or "").split(",")}
    if "text/event-stream" not in media_types:
        raise A2AError("content_type_not_supported", "Accept must include text/event-stream.", status_code=406)


def _bounded_int(
    value: str | None,
    *,
    name: str,
    minimum: int,
    maximum: int | None = None,
    default: int | None = None,
) -> int | None:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise A2AError("invalid_query_parameter", f"{name} must be an integer.", status_code=400) from error
    if parsed < minimum or (maximum is not None and parsed > maximum):
        boundary = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise A2AError("invalid_query_parameter", f"{name} must be {boundary}.", status_code=400)
    return parsed


def _task_state(value: str | None) -> a2a.TaskState | None:
    if value is None:
        return None
    try:
        selected = a2a.TaskState.Value(value)
    except ValueError as error:
        raise A2AError("invalid_query_parameter", "status is not a valid TaskState.", status_code=400) from error
    if selected == a2a.TASK_STATE_UNSPECIFIED:
        raise A2AError("invalid_query_parameter", "status must select a concrete TaskState.", status_code=400)
    return cast(a2a.TaskState, selected)


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise A2AError(
            "invalid_query_parameter",
            "statusTimestampAfter must be an ISO 8601 timestamp.",
            status_code=400,
        ) from error


def _boolean(value: str | None, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise A2AError("invalid_query_parameter", f"{name} must be true or false.", status_code=400)


def _configured_history_length(configuration: a2a.SendMessageConfiguration) -> int | None:
    return configuration.history_length if configuration.HasField("history_length") else None


def _json(message: Any, *, cache_control: str | None = None) -> JSONResponse:
    headers = {} if cache_control is None else {"Cache-Control": cache_control}
    return JSONResponse(
        MessageToDict(message, preserving_proto_field_name=False),
        media_type="application/a2a+json",
        headers=headers,
    )


def _public_card(message: a2a.AgentCard, *, if_none_match: str | None) -> Response:
    payload = MessageToDict(message, preserving_proto_field_name=False)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    etag = f'"{hashlib.sha256(encoded).hexdigest()}"'
    headers = {"Cache-Control": "public, max-age=300", "ETag": etag}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, media_type="application/a2a+json", headers=headers)


def _stream(events: AsyncIterator[a2a.StreamResponse]) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for event in events:
            payload = MessageToDict(event, preserving_proto_field_name=False)
            yield f"data: {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


def _error(error: Exception) -> JSONResponse:
    if isinstance(error, A2AError):
        code = error.status_code
        reason = error.code.upper()
        message = str(error)
    else:
        code = 400
        reason = "INVALID_REQUEST"
        message = "The A2A request body is invalid."
    status = {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        406: "INVALID_ARGUMENT",
        409: "FAILED_PRECONDITION",
        415: "INVALID_ARGUMENT",
        422: "INVALID_ARGUMENT",
    }.get(code, "INTERNAL")
    return JSONResponse(
        {
            "error": {
                "code": code,
                "status": status,
                "message": message,
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": reason,
                        "domain": "a2a-protocol.org",
                        "metadata": {},
                    }
                ],
            }
        },
        status_code=code,
        media_type="application/a2a+json",
    )


__all__ = ["router"]
