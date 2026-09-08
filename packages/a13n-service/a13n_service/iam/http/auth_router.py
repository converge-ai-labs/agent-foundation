"""Local login, session management, and single-use invitation acceptance."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretStr

from a13n_service.application_errors import ErrorCategory

from ..auth.passwords import csrf_token
from ..auth.sessions import Login
from ..schemas import AcceptInvitationRequest, AuthSession, LoginRequest, LoginResult, Page, PasswordRequest, User
from ..service_common import identity_error
from .authentication import SESSION_COOKIE, require_origin
from .dependencies import Actor, Pagination, identity, private_response

router = APIRouter(prefix="/api/v1", tags=["identity"], dependencies=[Depends(private_response)])


def login_response(response: Response, result: Login) -> LoginResult:
    response.set_cookie(SESSION_COOKIE, result.token, httponly=True, secure=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return LoginResult(user=result.user, session=result.session, csrf_token=csrf_token(result.token))


@router.post("/auth/login")
async def login(request: Request, response: Response, body: LoginRequest) -> LoginResult:
    runtime = identity(request)
    require_origin(request, runtime.configuration)
    result = await runtime.sessions.login(
        str(body.email), body.password.get_secret_value(), request_id=request.state.request_id
    )
    return login_response(response, result)


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(
    request: Request, response: Response, invitation_id: str, body: AcceptInvitationRequest
) -> LoginResult:
    runtime = identity(request)
    require_origin(request, runtime.configuration)
    result = await runtime.invitations.accept(invitation_id, body, request_id=request.state.request_id)
    return login_response(response, result)


@router.get("/auth/csrf")
async def browser_proof(request: Request, response: Response, actor: Actor) -> dict[str, str]:
    if actor.auth_method != "session":
        raise identity_error("browser_session_required", "A browser session is required.", ErrorCategory.forbidden)
    await identity(request).sessions.me(actor)
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf_token(request.cookies.get(SESSION_COOKIE, ""))}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, actor: Actor) -> None:
    await identity(request).sessions.revoke(actor, actor.credential_id)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"


@router.get("/users/me", response_model=User)
async def current_user(request: Request, actor: Actor) -> User:
    return await identity(request).sessions.me(actor)


@router.get("/users/me/auth-sessions", response_model=Page[AuthSession])
async def sessions(request: Request, actor: Actor, page: Pagination) -> Page[AuthSession]:
    return await identity(request).collections.sessions(actor, page)


@router.delete("/users/me/auth-sessions/{session_id}", status_code=204)
async def revoke_session(request: Request, actor: Actor, session_id: str) -> None:
    await identity(request).sessions.revoke(actor, session_id)


class ChangePasswordRequest(PasswordRequest):
    current_password: SecretStr = Field(min_length=1, max_length=128)


@router.post("/users/me/password", status_code=204)
async def change_password(request: Request, actor: Actor, body: ChangePasswordRequest) -> None:
    await identity(request).sessions.change_password(
        actor, body.current_password.get_secret_value(), body.password.get_secret_value()
    )
