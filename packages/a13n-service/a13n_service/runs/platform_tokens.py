"""Short-lived platform credentials issued only to a worker holding an accepted run's lease.

The encrypted claim contains no login credential or lease secret. Every request proves the current attempt,
then intersects current grants with the persisted acceptance ceiling. There is no public mint/refresh route.
"""

import base64
import hmac
from dataclasses import replace
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation, secret_hash
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.platform import PlatformGroup
from a13n_service.runs.attempts import AuthorityRevoked, Lease, authorize_execution, lock_lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.access import Authenticated, unauthenticated
from a13n_service.tenancy.authorize import ExecutionAuthority, Grant, WorkspaceScope, allowed_verbs

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


async def authenticate(runtime: Runtime, token: str) -> tuple[Authenticated, Claims]:
    claims = _decode(runtime.keys, token)
    async with transaction(runtime.storage) as session:
        credential = await _authenticate(session, runtime, claims)
    return credential, claims


async def _authenticate(session: AsyncSession, runtime: Runtime, claims: Claims) -> Authenticated:
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
    try:
        principal = await authorize_execution(session, runtime.access, run)
    except AuthorityRevoked:
        raise unauthenticated() from None
    ceiling = ExecutionAuthority.model_validate(run.authority)
    scope = WorkspaceScope(run.organization_id, run.workspace_id)
    verbs = allowed_verbs(principal, scope) & ceiling.verbs
    principal = replace(principal, confinement=scope, grants=(Grant(scope.organization_id, scope.workspace_id, verbs),))
    return Authenticated(principal, attempt.id, "run")
