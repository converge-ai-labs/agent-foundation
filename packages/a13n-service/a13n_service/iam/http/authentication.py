"""HTTP credential selection and same-origin browser proof validation."""

import hmac
import logging
from typing import Annotated

from anyio import fail_after
from fastapi import Depends, Request, Security
from fastapi.requests import HTTPConnection
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.request_runtime import get_process_runtime
from a13n_service.storage import short_session, transaction

from ..audit import AuthenticationAuditActor
from ..auth.credentials import expired, require_key_eligible
from ..auth.passwords import csrf_token, matches_token, token_hash
from ..authentication import AuthenticationError
from ..configuration import IdentityConfiguration
from ..domain import AuthenticatedActor, AuthorizationError, PrincipalRef, PrincipalType
from ..models import ApiKeyRecord, AuthSessionRecord, UserRecord
from ..service_common import audit, identity_error, singleton_organization

logger = logging.getLogger("a13n_service.iam.http_auth")

SESSION_COOKIE = "a13n_session"
CSRF_HEADER = "X-A13N-CSRF-Token"
WORKSPACE_HEADER = "X-A13N-Workspace-ID"


def require_origin(request: HTTPConnection, configuration: IdentityConfiguration) -> None:
    if request.headers.get("origin") != configuration.public_origin:
        raise identity_error("origin_rejected", "An accepted Origin is required.", ErrorCategory.forbidden)


def require_csrf(request: HTTPConnection, configuration: IdentityConfiguration) -> None:
    require_origin(request, configuration)
    token = request.cookies.get(configuration.session_cookie_name, "")
    proof = request.headers.get(CSRF_HEADER, "")
    if not token or len(token) > 128 or not hmac.compare_digest(proof.encode(), csrf_token(token).encode()):
        raise identity_error("csrf_rejected", "A valid browser proof is required.", ErrorCategory.forbidden)


class DatabaseAuthenticator:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], configuration: IdentityConfiguration) -> None:
        self._sessions = sessions
        self.configuration = configuration

    async def __call__(self, request: HTTPConnection) -> AuthenticatedActor:
        try:
            return await self._authenticate(request)
        except (AuthenticationError, ApplicationError):
            try:
                with fail_after(2):
                    async with transaction(self._sessions) as session:
                        audit(
                            session,
                            actor=AuthenticationAuditActor(
                                None, "unknown", None, getattr(request.state, "request_id", None)
                            ),
                            action="auth.authenticate",
                            resource_type="credential",
                            resource_id=None,
                            organization_id=None,
                            outcome="failure",
                        )
            except Exception:
                logger.warning("iam_authentication_audit_failed")
            raise

    async def _authenticate(self, request: HTTPConnection) -> AuthenticatedActor:
        authorization = request.headers.get("authorization")
        cookie = request.cookies.get(self.configuration.session_cookie_name)
        if (authorization is None) == (cookie is None):
            raise AuthenticationError("exactly one credential required")
        async with short_session(self._sessions) as session:
            if authorization is not None:
                actor = await self._key(session, authorization, request)
            else:
                assert cookie is not None
                actor = await self._session(session, cookie, request)
        if cookie is not None:
            if request.scope["type"] == "websocket":
                require_origin(request, self.configuration)
            elif request.scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                require_csrf(request, self.configuration)
        return actor

    async def _key(self, session: AsyncSession, authorization: str, request: HTTPConnection) -> AuthenticatedActor:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.startswith("afk_") or len(value) > 256:
            raise AuthenticationError("invalid credential")
        key_id, separator, secret = value[4:].partition(".")
        key = await session.get(ApiKeyRecord, key_id)
        if key is None or not separator or not matches_token(secret, key.secret_hash):
            raise AuthenticationError("invalid credential")
        try:
            await require_key_eligible(session, key)
        except AuthorizationError as error:
            raise AuthenticationError("invalid credential") from error
        requested = request.headers.get(WORKSPACE_HEADER)
        if requested is not None and requested != key.boundary_id:
            raise AuthenticationError("invalid credential boundary")
        return AuthenticatedActor(
            principal=PrincipalRef(principal_type=PrincipalType(key.principal_type), principal_id=key.principal_id),
            auth_method="api_key",
            credential_id=key.id,
            boundary_workspace_id=key.boundary_id,
            request_id=getattr(request.state, "request_id", None),
            credential_source="service",
        )

    async def _session(self, session: AsyncSession, token: str, request: HTTPConnection) -> AuthenticatedActor:
        if not 32 <= len(token) <= 128:
            raise AuthenticationError("invalid credential")
        row = await session.scalar(select(AuthSessionRecord).where(AuthSessionRecord.token_hash == token_hash(token)))
        if row is None or row.revoked_at is not None or expired(row.expires_at):
            raise AuthenticationError("invalid credential")
        user = await session.get(UserRecord, row.user_id)
        if user is None or user.status != "active":
            raise AuthenticationError("invalid credential")
        organization = await singleton_organization(session)
        requested = request.headers.get(WORKSPACE_HEADER)
        workspace = requested
        if request.path_params.get("organization") is not None and workspace is not None:
            raise AuthenticationError("invalid credential boundary")
        return AuthenticatedActor(
            principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=user.id),
            auth_method="session",
            credential_id=row.id,
            boundary_workspace_id=workspace,
            boundary_organization_id=organization.id if workspace is None else None,
            request_id=getattr(request.state, "request_id", None),
            credential_source="service",
        )


async def authenticate_request(
    request: Request,
    _bearer: Annotated[
        HTTPAuthorizationCredentials | None, Security(HTTPBearer(scheme_name="BearerAuth", auto_error=False))
    ] = None,
    _session: Annotated[
        str | None, Security(APIKeyCookie(name=SESSION_COOKIE, scheme_name="SessionAuth", auto_error=False))
    ] = None,
) -> AuthenticatedActor:
    runtime = get_process_runtime(request)
    authenticator = runtime.request_authenticator if runtime is not None else None
    if authenticator is None:
        raise AuthenticationError("authentication is not configured")
    try:
        actor = await authenticator(request)
    except AuthenticationError:
        raise
    except ApplicationError:
        raise
    except Exception as error:
        raise AuthenticationError("authentication failed") from error
    request.state.actor = actor
    return actor


async def authenticate_mutation(
    request: Request, actor: Annotated[AuthenticatedActor, Depends(authenticate_request)]
) -> AuthenticatedActor:
    """Require browser proof even for protocol callbacks historically exposed as GET."""
    if actor.credential_source == "service" and actor.auth_method == "session":
        runtime = get_process_runtime(request)
        assert runtime is not None
        require_csrf(request, runtime.settings.identity_configuration())
    return actor
