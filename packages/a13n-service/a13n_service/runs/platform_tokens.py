"""Short-lived platform credentials issued only to a worker holding an accepted run's lease.

The encrypted claim contains no login credential or lease secret. Every request proves the current attempt,
then intersects current grants with the persisted acceptance ceiling. There is no public mint/refresh route.
"""

import base64
import hmac
from dataclasses import dataclass, field, replace
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation, secret_hash
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.platform import PlatformAccess, PlatformGroup
from a13n_service.runs.attempts import AuthorityRevoked, Lease, authorize_execution, lock_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.access import (
    Authenticated,
    Authenticator,
    login_session_required,
    require_active_workspace,
    unauthenticated,
)
from a13n_service.tenancy.authorize import ExecutionAuthority

PREFIX = "a13n_run_"
_LOCATION = SecretLocation(None, "run_platform_tokens", "platform_operations_v1", "service")
_LIFETIME = timedelta(minutes=5)


class Claims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(min_length=1, max_length=72)
    attempt_id: str = Field(min_length=1, max_length=72)
    lease_hash: str = Field(min_length=64, max_length=64)
    groups: frozenset[PlatformGroup]
    expires_at: float


@dataclass(frozen=True, slots=True, kw_only=True)
class RunCredential(Authenticated):
    claims: Claims = field(repr=False)


class RunAuthenticator:
    """Authenticate Run credentials; delegate other credentials to the Distribution's authenticator."""

    def __init__(self, delegate: Authenticator):
        self.delegate = delegate

    async def authenticate(self, request: Request, response: Response) -> Authenticated | None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token.startswith(PREFIX):
            return await authenticate(request.app.state.runtime, token)
        return await self.delegate.authenticate(request, response)

    async def recheck(self, session: AsyncSession, credential: Authenticated) -> None:
        if isinstance(credential, RunCredential):
            run = await _live_run(session, credential.claims)
            await require_active_workspace(session, run.workspace_id)
        else:
            await self.delegate.recheck(session, credential)

    async def logout(self, request: Request, response: Response, credential: Authenticated) -> None:
        if isinstance(credential, RunCredential):
            raise login_session_required()
        await self.delegate.logout(request, response, credential)


def _decode(keys: KeyRing, token: str) -> Claims:
    try:
        if not token.startswith(PREFIX) or len(token) > 8192:
            raise ValueError("Invalid token")
        encoded = token[len(PREFIX) :]
        envelope = Envelope.model_validate_json(base64.b64decode(encoded, altchars=b"-_", validate=True))
        return Claims.model_validate_json(keys.reveal(envelope, _LOCATION))
    except (ValueError, ValidationError, ServiceError):
        raise unauthenticated() from None


async def issue(runtime: Runtime, lease: Lease, groups: frozenset[PlatformGroup]) -> str:
    """Called with the Host's frozen selection, never model-provided identity or scope."""
    async with transaction(runtime.storage) as session:
        run, _, current = await lock_lease(session, lease)
        if run.cancel_requested_at is not None:
            raise unauthenticated()
        await authorize_execution(session, runtime.access, run)
        claims = Claims(
            run_id=run.id,
            attempt_id=lease.attempt_id,
            lease_hash=secret_hash(lease.token),
            groups=groups,
            expires_at=(current + _LIFETIME).timestamp(),
        )
    envelope = runtime.keys.protect(claims.model_dump_json().encode(), _LOCATION)
    return PREFIX + base64.urlsafe_b64encode(envelope.model_dump_json().encode()).decode()


async def authenticate(runtime: Runtime, token: str) -> RunCredential:
    claims = _decode(runtime.keys, token)
    async with transaction(runtime.storage) as session:
        run = await _live_run(session, claims)
        try:
            principal = await authorize_execution(session, runtime.access, run)
        except AuthorityRevoked:
            raise unauthenticated() from None
        credential = RunCredential(
            principal,
            claims.attempt_id,
            "run",
            authority=ExecutionAuthority.model_validate(run.authority),
            platform=PlatformAccess(claims.groups, claims.run_id, claims.attempt_id),
            claims=claims,
        )
        return replace(credential, principal=credential.constrain(principal))


async def _live_run(session: AsyncSession, claims: Claims) -> RunRow:
    run = await session.get(RunRow, claims.run_id)
    attempt = await session.get(AttemptRow, claims.attempt_id)
    current = await now(session)
    if (
        run is None
        or attempt is None
        or claims.expires_at <= current.timestamp()
        or run.status != "running"
        or run.cancel_requested_at is not None
        or run.current_attempt_id != attempt.id
        or attempt.run_id != run.id
        or attempt.status not in {"leased", "running"}
        or attempt.lease_expires_at <= current
        or not hmac.compare_digest(attempt.lease_token_hash, claims.lease_hash)
    ):
        raise unauthenticated()
    return run
