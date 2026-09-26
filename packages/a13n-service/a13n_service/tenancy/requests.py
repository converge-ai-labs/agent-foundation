"""The assembled runtime, who is calling, and the images they send, as FastAPI dependencies; they return values
and never yield SQL sessions."""

from typing import Annotated

from fastapi import Depends, Request

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import answer_headers
from a13n_service.infra.redis import rate_limit
from a13n_service.tenancy.access import Authenticated, unauthenticated
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.runtime import Runtime


async def current_runtime(request: Request) -> Runtime:
    """The runtime serving `request`; code that holds a request instead of a dependency awaits this directly."""
    return request.app.state.runtime


CurrentRuntime = Annotated[Runtime, Depends(current_runtime)]


async def current_credential(request: Request, runtime: CurrentRuntime) -> Authenticated:
    """The request's credential. What authentication sets on the answer, such as a renewed login cookie, and
    `Cache-Control: no-store` reach every response to the request, including a route's own and an error."""
    answer = answer_headers(request)
    credential = await runtime.access.authenticator.authenticate(request, answer)
    if credential is None:
        raise unauthenticated()
    answer.headers["Cache-Control"] = "no-store"
    return credential


async def current_principal(credential: Annotated[Authenticated, Depends(current_credential)]) -> Principal:
    return credential.principal


Credential = Annotated[Authenticated, Depends(current_credential)]
Actor = Annotated[Principal, Depends(current_principal)]


async def limit_guessing(request: Request, flow: str, *identities: str) -> None:
    """Bounded attempts per client address and per named identity for each credential-guessing flow."""
    runtime = await current_runtime(request)
    config = runtime.settings.auth
    peer = request.client.host if request.client else "unknown"
    for identity in (f"peer:{peer}", *identities):
        await rate_limit(
            runtime.redis, f"{flow}:{identity}", limit=config.login_limit, window_seconds=config.login_window_seconds
        )


async def limit_uploads(request: Request, principal_id: str) -> None:
    """One per-principal budget for workspace uploads and images, since stored bytes are never reclaimed."""
    runtime = await current_runtime(request)
    limits = runtime.settings.objects
    await rate_limit(
        runtime.redis, f"upload:{principal_id}", limit=limits.upload_limit, window_seconds=limits.upload_window_seconds
    )


async def image_body(request: Request, actor: Actor, runtime: CurrentRuntime) -> bytes:
    """An image sent as the raw request body, bounded and budgeted like an upload."""
    await limit_uploads(request, actor.id)
    limits = runtime.settings.objects
    data = await request.body()
    bound = min(limits.upload_bytes, limits.max_bytes)
    if len(data) > bound:
        raise ServiceError("payload_too_large", "Image exceeds its byte limit", {"limit": bound})
    return data


ImageBody = Annotated[bytes, Depends(image_body)]
