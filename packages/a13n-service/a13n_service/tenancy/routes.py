"""The public account flows and the caller's own account over HTTP."""

from fastapi import APIRouter, Request, Response

from a13n_service.infra import images
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.settings import Settings
from a13n_service.tenancy import api_keys, users
from a13n_service.tenancy.access import Authenticated, login_session_required
from a13n_service.tenancy.audit import list_account_events
from a13n_service.tenancy.authenticate import (
    Login,
    check_origin,
    login,
    session_cookie,
    session_csrf,
    set_session_cookie,
)
from a13n_service.tenancy.bootstrap import AlreadyBootstrapped, BootstrapInput, bootstrap, initialized
from a13n_service.tenancy.invitations import accept_invitation
from a13n_service.tenancy.requests import Actor, Credential, CurrentRuntime, ImageBody, limit_guessing
from a13n_service.tenancy.schemas import (
    AccountDisable,
    ApiKey,
    ApiKeyPage,
    AuditPage,
    AuthConfiguration,
    EmailChangeConfirm,
    InvitationAccept,
    IssuedKey,
    LoginInput,
    LoginOutput,
    LoginSessionPage,
    PasswordChange,
    PasswordReset,
    PasswordResetConfirm,
    Profile,
    ProfileUpdate,
    SessionProfile,
    UserKeyCreate,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])


async def _limit_password_check(request: Request, actor_id: str) -> None:
    """Every route that verifies the caller's current password shares one budget."""
    await limit_guessing(request, "current_password", f"principal:{actor_id}")


def _session_id(credential: Authenticated) -> str | None:
    return credential.credential_id if credential.kind == "session" else None


def _signed_in(response: Response, result: Login, settings: Settings) -> LoginOutput:
    set_session_cookie(response, result.secret, settings)
    response.headers["Cache-Control"] = "no-store"
    return LoginOutput(principal_id=result.principal.id, csrf_token=result.csrf_token)


@router.post("/auth/login", response_model=LoginOutput)
async def password_login(
    request: Request, response: Response, body: LoginInput, runtime: CurrentRuntime
) -> LoginOutput:
    check_origin(request, runtime.settings)
    await limit_guessing(request, "login", f"email:{body.email}")
    result = await login(
        runtime.storage,
        runtime.access,
        email=body.email,
        password=body.password.get_secret_value(),
        session_seconds=runtime.settings.auth.session_seconds,
    )
    return _signed_in(response, result, runtime.settings)


@router.post("/auth/bootstrap", response_model=LoginOutput)
async def bootstrap_administrator(
    request: Request, response: Response, body: BootstrapInput, runtime: CurrentRuntime
) -> LoginOutput:
    """Public only until initialized: creates the first administrator, as the `bootstrap` command does, signed in."""
    check_origin(request, runtime.settings)
    await limit_guessing(request, "bootstrap")
    already = ServiceError("already_exists", "The Service is already initialized", {"kind": "organization"})
    # Checked before hashing, so an initialized Service spends nothing on the attempt.
    if await initialized(runtime.storage):
        raise already
    try:
        await bootstrap(runtime.storage, body)
    except AlreadyBootstrapped:
        raise already from None
    result = await login(
        runtime.storage,
        runtime.access,
        email=body.email,
        password=body.password.get_secret_value(),
        session_seconds=runtime.settings.auth.session_seconds,
    )
    return _signed_in(response, result, runtime.settings)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, credential: Credential, runtime: CurrentRuntime) -> None:
    await runtime.access.authenticator.logout(request, response, credential)


@router.get("/auth/session", response_model=SessionProfile)
async def session_profile(request: Request, credential: Credential, runtime: CurrentRuntime) -> SessionProfile:
    """Restores a browser session; the CSRF token is stable for the session's lifetime."""
    secret = request.cookies.get(session_cookie(runtime.settings))
    if credential.kind != "session" or secret is None:
        raise login_session_required()
    user = await users.get_profile(runtime.storage, credential.principal)
    return SessionProfile(user=user, csrf_token=session_csrf(secret))


@router.get("/auth/configuration", response_model=AuthConfiguration)
async def auth_configuration(runtime: CurrentRuntime) -> AuthConfiguration:
    return AuthConfiguration(
        email_delivery=runtime.settings.auth.mail.smtp_host is not None,
        initialized=await initialized(runtime.storage),
    )


@router.post("/auth/password-reset", status_code=204)
async def request_password_reset(request: Request, body: PasswordReset, runtime: CurrentRuntime) -> None:
    check_origin(request, runtime.settings)
    await limit_guessing(request, "password_reset", f"email:{body.email}")
    await users.request_password_reset(runtime.storage, runtime.keys, runtime.settings, body.email)


@router.post("/auth/password-reset/confirm", status_code=204)
async def confirm_password_reset(request: Request, body: PasswordResetConfirm, runtime: CurrentRuntime) -> None:
    check_origin(request, runtime.settings)
    await limit_guessing(request, "password_reset_confirm")
    await users.confirm_password_reset(runtime.storage, body)


@router.post("/auth/email-change/confirm", status_code=204)
async def confirm_email_change(request: Request, body: EmailChangeConfirm, runtime: CurrentRuntime) -> None:
    check_origin(request, runtime.settings)
    await limit_guessing(request, "email_change_confirm")
    await users.confirm_email_change(runtime.storage, body.token)


@router.post("/invitations/{invitation_id}/accept", response_model=LoginOutput)
async def accept(
    request: Request, response: Response, invitation_id: str, body: InvitationAccept, runtime: CurrentRuntime
) -> LoginOutput:
    """Public by token: creates or joins the invited account and starts a login session."""
    check_origin(request, runtime.settings)
    await limit_guessing(request, "invitation_accept", f"invitation:{invitation_id}")
    result = await accept_invitation(runtime.storage, runtime.access, runtime.settings, invitation_id, body)
    return _signed_in(response, result, runtime.settings)


@router.get("/users/me", response_model=Profile, tags=["tenancy"])
async def get_profile(response: Response, actor: Actor, runtime: CurrentRuntime) -> Profile:
    return tagged(response, await users.get_profile(runtime.storage, actor))


@router.patch("/users/me", response_model=Profile, tags=["tenancy"])
async def update_profile(
    request: Request,
    response: Response,
    body: ProfileUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Profile:
    if body.email is not None:
        await _limit_password_check(request, actor.id)
    return tagged(
        response,
        await users.update_profile(runtime.storage, runtime.keys, runtime.settings, actor, body, if_match=if_match),
    )


@router.put("/users/me/avatar", response_model=Profile, tags=["tenancy"], openapi_extra=images.UPLOAD)
async def put_avatar(
    response: Response, data: ImageBody, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Profile:
    return tagged(response, await users.change_avatar(runtime.storage, runtime.objects, actor, data, if_match=if_match))


@router.delete("/users/me/avatar", response_model=Profile, tags=["tenancy"])
async def delete_avatar(response: Response, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None) -> Profile:
    return tagged(response, await users.change_avatar(runtime.storage, runtime.objects, actor, None, if_match=if_match))


@router.get("/users/{user_id}/avatar", response_class=Response, responses=images.CONTENT, tags=["tenancy"])
async def get_avatar(user_id: str, actor: Actor, runtime: CurrentRuntime) -> Response:
    return await images.serve(runtime.objects, user_id, await users.get_avatar(runtime.storage, actor, user_id))


@router.get("/users/me/audit-events", response_model=AuditPage, tags=["tenancy"])
async def list_account_audit_events(
    actor: Actor, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> AuditPage:
    """The caller's own trail, account-wide events included; requires a login session."""
    return await list_account_events(runtime.storage, actor, limit=limit, cursor=cursor)


@router.post("/users/me/password", status_code=204, tags=["tenancy"])
async def change_password(
    request: Request, body: PasswordChange, credential: Credential, runtime: CurrentRuntime
) -> None:
    await _limit_password_check(request, credential.principal.id)
    await users.change_password(runtime.storage, credential.principal, body, current_session_id=_session_id(credential))


@router.post("/users/me/disable", status_code=204, tags=["tenancy"])
async def disable_account(request: Request, body: AccountDisable, actor: Actor, runtime: CurrentRuntime) -> None:
    """Disable the caller's own account, proven by the current password; no route enables it again."""
    await _limit_password_check(request, actor.id)
    await users.disable_account(runtime.storage, runtime.access, actor, body)


@router.get("/users/me/login-sessions", response_model=LoginSessionPage, tags=["tenancy"])
async def list_login_sessions(
    credential: Credential, runtime: CurrentRuntime, limit: PageLimit = 50, cursor: str | None = None
) -> LoginSessionPage:
    return await users.list_login_sessions(
        runtime.storage,
        credential.principal,
        current_session_id=_session_id(credential),
        limit=limit,
        cursor=cursor,
    )


@router.delete("/users/me/login-sessions/{session_id}", status_code=204, tags=["tenancy"])
async def revoke_login_session(session_id: str, actor: Actor, runtime: CurrentRuntime) -> None:
    await users.revoke_login_session(runtime.storage, actor, session_id)


@router.get("/users/me/keys", response_model=ApiKeyPage, tags=["tenancy"])
async def list_user_keys(
    actor: Actor,
    runtime: CurrentRuntime,
    workspace_id: str | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ApiKeyPage:
    return await api_keys.list_user_keys(runtime.storage, actor, workspace_id=workspace_id, limit=limit, cursor=cursor)


@router.post("/users/me/keys", response_model=IssuedKey, status_code=201, tags=["tenancy"])
async def create_user_key(body: UserKeyCreate, actor: Actor, runtime: CurrentRuntime) -> IssuedKey:
    """Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation."""
    return await api_keys.create_user_key(runtime.storage, runtime.access, actor, body)


@router.delete("/users/me/keys/{key_id}", response_model=ApiKey, tags=["tenancy"])
async def revoke_user_key(
    response: Response, key_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> ApiKey:
    return tagged(response, await api_keys.revoke_user_key(runtime.storage, actor, key_id, if_match=if_match))
