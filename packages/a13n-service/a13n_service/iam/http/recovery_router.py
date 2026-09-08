"""Pre-principal reset proofs and authenticated email changes."""

from fastapi import APIRouter, Depends, Request

from ..profile_schemas import (
    AuthConfiguration,
    CompleteEmailChangeRequest,
    CompletePasswordResetRequest,
    EmailChangeRequest,
    PasswordResetRequest,
)
from .authentication import require_origin
from .dependencies import Actor, identity, private_response

router = APIRouter(prefix="/api/v1", tags=["identity-recovery"], dependencies=[Depends(private_response)])


@router.get("/auth/configuration", response_model=AuthConfiguration)
async def auth_configuration(request: Request) -> AuthConfiguration:
    return AuthConfiguration(email_delivery=bool(identity(request).configuration.smtp_host))


@router.post("/auth/password-reset", status_code=202)
async def request_password_reset(request: Request, body: PasswordResetRequest) -> None:
    runtime = identity(request)
    require_origin(request, runtime.configuration)
    await runtime.recovery.request_reset(str(body.email))


@router.post("/auth/password-reset/complete", status_code=204)
async def complete_password_reset(request: Request, body: CompletePasswordResetRequest) -> None:
    runtime = identity(request)
    require_origin(request, runtime.configuration)
    await runtime.recovery.complete_reset(
        body.token.get_secret_value(), body.password.get_secret_value(), request.state.request_id
    )


@router.post("/users/me/email-change", status_code=202)
async def request_email_change(request: Request, actor: Actor, body: EmailChangeRequest) -> None:
    await identity(request).recovery.request_email_change(
        actor, str(body.email), body.current_password.get_secret_value()
    )


@router.post("/users/me/email-change/complete", status_code=204)
async def complete_email_change(request: Request, actor: Actor, body: CompleteEmailChangeRequest) -> None:
    await identity(request).recovery.complete_email_change(actor, body.token.get_secret_value())
