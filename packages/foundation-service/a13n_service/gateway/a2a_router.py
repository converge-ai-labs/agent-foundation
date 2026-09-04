"""A2A 1.0 HTTP+JSON transport routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from a2a.types import a2a_pb2 as a2a
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from google.protobuf.json_format import MessageToDict, Parse, ParseError

from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime

from .a2a import A2AError, A2AService

router = APIRouter(tags=["a2a"])
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
        return _json(card, cache_control="public, max-age=300")
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
            task = await _service(request).wait_task(actor=actor, agent_id=agent_id, task_id=task.id)
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
        events = _service(request).stream_task(actor=actor, agent_id=agent_id, task_id=task.id)
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
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        task = await _service(request).get_task(actor=actor, agent_id=agent_id, task_id=task_id)
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
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 50,
) -> Response:
    try:
        _validate_wire(version=version, extensions=extensions, content_type=None, body_required=False)
        tasks = await _service(request).list_tasks(
            actor=actor,
            agent_id=agent_id,
            context_id=context_id,
            limit=page_size,
        )
        return _json(a2a.ListTasksResponse(tasks=tasks, page_size=page_size, total_size=len(tasks)))
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


def _json(message: Any, *, cache_control: str | None = None) -> JSONResponse:
    headers = {} if cache_control is None else {"Cache-Control": cache_control}
    return JSONResponse(
        MessageToDict(message, preserving_proto_field_name=False),
        media_type="application/a2a+json",
        headers=headers,
    )


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
