"""HTTP credential selection and same-origin browser proof validation."""

import hmac
import logging

from anyio import fail_after
from fastapi.requests import HTTPConnection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.storage import short_session, transaction

from .audit import AuthenticationAuditActor
from .auth_models import ApiKeyRecord, AuthSessionRecord
from .authentication import AuthenticationError
from .authorization import AuthenticatedActor, AuthorizationError
from .configuration import IdentityConfiguration
from .credentials import expired, require_key_eligible
from .domain import PrincipalRef, PrincipalType
from .models import UserRecord
from .passwords import csrf_token, matches_token, token_hash
from .service_common import audit, identity_error, singleton_organization

logger = logging.getLogger("a13n_service.iam.http_auth")

SESSION_COOKIE = "a13n_session"
CSRF_HEADER = "X-A13N-CSRF-Token"
WORKSPACE_HEADER = "X-A13N-Workspace-ID"


def require_origin(request: HTTPConnection, configuration: IdentityConfiguration) -> None:
    if request.headers.get("origin") != configuration.public_origin:
        raise identity_error("origin_rejected", "An accepted Origin is required.", ErrorCategory.forbidden)


def require_csrf(request: HTTPConnection, configuration: IdentityConfiguration) -> None:
    require_origin(request, configuration)
    token = request.cookies.get(SESSION_COOKIE, "")
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
        cookie = request.cookies.get(SESSION_COOKIE)
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
        workspace_path = request.path_params.get("workspace_id")
        if requested is not None and workspace_path is not None and requested != workspace_path:
            raise AuthenticationError("invalid credential boundary")
        workspace = requested
        if request.path_params.get("organization_id") is not None and workspace is not None:
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
