"""The local authenticator: password login, login-session cookies and API keys.

Only credential lookup lives here. Membership, key, service-account and profile management are separate
tenancy functions taking a `Principal`, so they keep working when a distribution replaces authentication.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access, Authenticated, login_session_required, principal_for, unauthenticated
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.credentials import issue_token, verify_password
from a13n_service.tenancy.requests import current_runtime
from a13n_service.tenancy.tables import ApiKeyRow, PasswordRow, PrincipalRow, TokenRow
from a13n_service.tenancy.users import revoke_login_session

# Over HTTPS the `__Host-` prefix binds the session cookie to this exact host; plain HTTP cannot carry the prefix,
# which requires a Secure cookie.
COOKIE_NAME = "__Host-a13n_session"
HTTP_COOKIE_NAME = "a13n_session"
# Every unsafe request authenticated by the login-session cookie also sends the session's CSRF token.
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Rolling expiry and key usage are written at most this often, not on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)

type LocalKind = Literal["session", "key"]


def session_csrf(secret: str) -> str:
    """Stable per login session and derivable from its cookie, so nothing recoverable is stored."""
    return hmac.new(secret.encode(), b"a13n:session:csrf:v1", hashlib.sha256).hexdigest()


def check_origin(request: Request, settings: Settings) -> None:
    """A browser may change state only from the service's own public origins."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.server.public_origins:
        raise ServiceError("forbidden", "Request origin is not allowed")


def session_cookie(settings: Settings) -> str:
    return COOKIE_NAME if settings.server.https else HTTP_COOKIE_NAME


def set_session_cookie(response: Response, secret: str, settings: Settings) -> None:
    response.set_cookie(
        session_cookie(settings),
        secret,
        max_age=settings.auth.session_seconds,
        secure=settings.server.https,
        httponly=True,
        samesite="strict",
        path="/",
    )


async def _live_credential(
    session: AsyncSession, kind: str, *, digest: str | None = None, credential_id: str | None = None
) -> TokenRow | ApiKeyRow:
    """One definition of a usable local credential, shared by authentication and re-checks."""
    credential: TokenRow | ApiKeyRow | None = None
    if kind == "session":
        credential = await session.scalar(
            select(TokenRow).where(
                TokenRow.secret_hash == digest if digest is not None else TokenRow.id == credential_id,
                TokenRow.kind == "session",
                TokenRow.revoked_at.is_(None),
                TokenRow.expires_at > func.clock_timestamp(),
            )
        )
    elif kind == "key":
        credential = await session.scalar(
            select(ApiKeyRow).where(
                ApiKeyRow.secret_hash == digest if digest is not None else ApiKeyRow.id == credential_id,
                ApiKeyRow.revoked_at.is_(None),
                or_(ApiKeyRow.expires_at.is_(None), ApiKeyRow.expires_at > func.clock_timestamp()),
            )
        )
    if credential is None:
        raise unauthenticated()
    return credential


async def authenticate_secret(
    storage: Storage, access: Access, secret: str, kind: LocalKind, *, session_seconds: int
) -> Authenticated:
    if not secret or len(secret) > 512:
        raise unauthenticated()
    async with transaction(storage) as session:
        credential = await _live_credential(session, kind, digest=secret_hash(secret))
        confinement = (
            WorkspaceScope(credential.organization_id, credential.workspace_id)
            if isinstance(credential, ApiKeyRow)
            else None
        )
        principal = await principal_for(session, access, credential.principal_id, confinement=confinement)
        current = await now(session)
        if isinstance(credential, TokenRow):
            if credential.expires_at < current + timedelta(seconds=session_seconds) - _TOUCH_INTERVAL:
                credential.expires_at = current + timedelta(seconds=session_seconds)
        elif credential.last_used_at is None or credential.last_used_at <= current - _TOUCH_INTERVAL:
            credential.last_used_at = current
        return Authenticated(principal, credential.id, kind)


class LocalAuthenticator:
    """`Authorization: Bearer <api key>` or the login-session cookie; cookie mutations prove Origin and CSRF."""

    async def authenticate(self, request: Request, response: Response) -> Authenticated | None:
        runtime = await current_runtime(request)
        seconds = runtime.settings.auth.session_seconds
        authorization = request.headers.get("authorization")
        if authorization is not None:
            scheme, _, secret = authorization.partition(" ")
            if scheme.lower() != "bearer":
                raise unauthenticated()
            return await authenticate_secret(
                runtime.storage, runtime.access, secret.strip(), "key", session_seconds=seconds
            )
        secret = request.cookies.get(session_cookie(runtime.settings))
        if secret is None:
            return None
        if request.method not in SAFE_METHODS:
            check_origin(request, runtime.settings)
            presented = request.headers.get(CSRF_HEADER, "")
            if not hmac.compare_digest(session_csrf(secret).encode(), presented.encode()):
                raise ServiceError("forbidden", "CSRF validation failed")
        credential = await authenticate_secret(
            runtime.storage, runtime.access, secret, "session", session_seconds=seconds
        )
        # Rolling expiry: the server extends the session, the browser keeps the cookie as long.
        set_session_cookie(response, secret, runtime.settings)
        return credential

    async def recheck(self, session: AsyncSession, credential: Authenticated) -> None:
        await _live_credential(session, credential.kind, credential_id=credential.credential_id)

    async def logout(self, request: Request, response: Response, credential: Authenticated) -> None:
        if credential.kind != "session":
            raise login_session_required()
        runtime = await current_runtime(request)
        await revoke_login_session(runtime.storage, credential.principal, credential.credential_id)
        response.delete_cookie(
            session_cookie(runtime.settings),
            secure=runtime.settings.server.https,
            httponly=True,
            samesite="strict",
            path="/",
        )


@dataclass(frozen=True, slots=True)
class Login:
    principal: Principal
    secret: str
    csrf_token: str


async def open_session(session: AsyncSession, principal_id: str, *, seconds: int) -> str:
    """Issue a login-session token in the caller's transaction and return its cookie secret."""
    token, secret = await issue_token(session, principal_id, "session", seconds=seconds)
    record(
        session,
        None,
        actor_id=principal_id,
        action="login_session.create",
        target_kind="login_session",
        target_id=token.id,
    )
    return secret


async def login(storage: Storage, access: Access, *, email: str, password: str, session_seconds: int) -> Login:
    async with short_session(storage) as session:
        stored = (
            await session.execute(
                select(PrincipalRow.id, PasswordRow.hash)
                .join(PasswordRow, PrincipalRow.id == PasswordRow.principal_id)
                .where(PrincipalRow.email == email, PrincipalRow.status == "active")
            )
        ).one_or_none()
    principal_id, password_hash = stored if stored is not None else (None, None)
    verified = await verify_password(password_hash, password)
    if principal_id is None or not verified:
        raise ServiceError("unauthenticated", "Invalid email or password")
    async with transaction(storage) as session:
        # The password may have changed while it was verified outside the transaction.
        current = await lock(session, PasswordRow, principal_id)
        if current is None or current.hash != password_hash:
            raise ServiceError("unauthenticated", "Invalid email or password")
        principal = await principal_for(session, access, principal_id)
        secret = await open_session(session, principal_id, seconds=session_seconds)
    return Login(principal, secret, session_csrf(secret))
