"""Provider data-plane ingress under ``/connectivity/v1``."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from a13n_service.connectivity.errors import NativeError
from a13n_service.request_runtime import get_connectivity_data_runtime, get_process_runtime

from .admission import IngressEventService
from .provider import ProviderRequest

router = APIRouter(prefix="/connectivity/v1", tags=["connectivity-ingress"])


def _service(request: Request) -> IngressEventService:
    runtime = get_connectivity_data_runtime(request)
    if runtime is None:
        raise NativeError("ingress_unavailable", "Ingress is unavailable.", status_code=503)
    return runtime.ingress_events


@router.post("/ingresses/{ingress_id}/events", include_in_schema=False)
async def receive_ingress_event(request: Request, ingress_id: str) -> Response:
    runtime = get_process_runtime(request)
    if runtime is None:
        raise NativeError("ingress_unavailable", "Ingress is unavailable.", status_code=503)
    body = await _read_body(request, runtime.settings.connectivity_provider_request_max_bytes)
    try:
        provider_request = ProviderRequest(
            headers={key.lower(): value for key, value in request.headers.items()},
            body=body,
            content_type=request.headers.get("content-type"),
        )
    except ValidationError as error:
        raise NativeError("invalid_request", "Provider request metadata is invalid.", status_code=400) from error
    provider_response = await _service(request).receive(
        ingress_id=ingress_id,
        request=provider_request,
    )
    return Response(
        content=provider_response.body,
        status_code=provider_response.status_code,
        headers=provider_response.headers,
    )


async def _read_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0:
                raise NativeError("invalid_request", "Content-Length is invalid.", status_code=400)
            if parsed_length > max_bytes:
                raise NativeError("request_too_large", "Provider request is too large.", status_code=413)
        except ValueError as error:
            raise NativeError("invalid_request", "Content-Length is invalid.", status_code=400) from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise NativeError("request_too_large", "Provider request is too large.", status_code=413)
    return bytes(body)
