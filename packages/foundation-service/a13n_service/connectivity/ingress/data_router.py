"""Provider data-plane ingress under ``/connectivity/v1``."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from .admission import IngressEventService
from .errors import IngressError
from .provider import ProviderRequest

router = APIRouter(prefix="/connectivity/v1", tags=["connectivity-ingress"])


def _service(request: Request) -> IngressEventService:
    service: IngressEventService | None = getattr(request.app.state, "ingress_event_service", None)
    if service is None:
        raise IngressError("ingress_unavailable", "Ingress is unavailable.", status_code=503)
    return service


@router.post("/ingresses/{ingress_id}/events", include_in_schema=False)
async def receive_ingress_event(request: Request, ingress_id: str) -> Response:
    body = await _read_body(request, request.app.state.settings.connectivity_provider_request_max_bytes)
    try:
        provider_request = ProviderRequest(
            headers={key.lower(): value for key, value in request.headers.items()},
            body=body,
            content_type=request.headers.get("content-type"),
        )
    except ValidationError as error:
        raise IngressError("invalid_request", "Provider request metadata is invalid.", status_code=400) from error
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
                raise IngressError("invalid_request", "Content-Length is invalid.", status_code=400)
            if parsed_length > max_bytes:
                raise IngressError("request_too_large", "Provider request is too large.", status_code=413)
        except ValueError as error:
            raise IngressError("invalid_request", "Content-Length is invalid.", status_code=400) from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise IngressError("request_too_large", "Provider request is too large.", status_code=413)
    return bytes(body)
