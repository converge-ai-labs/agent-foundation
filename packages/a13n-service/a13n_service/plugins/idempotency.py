"""Plugin-specific idempotency policy over shared durable evidence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor

from .errors import PluginError


def plugin_key_digest(key: str) -> str:
    """Validate the shared visible ASCII HTTP key contract."""
    try:
        return digest_visible_ascii_key(key)
    except InvalidIdempotencyKey as error:
        raise PluginError(
            "invalid_request", "Idempotency-Key is invalid.", category=ErrorCategory.invalid_request
        ) from error


async def load_plugin_evidence(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    key_digest: str,
    request_digest: str,
    now: datetime,
) -> IdempotencyEvidenceRecord | None:
    return await load_evidence(
        session,
        scope=_scope(actor, operation=operation, scope_id=scope_id),
        identity=IdempotencyIdentity(key_digest, request_digest),
        now=now,
    )


def new_plugin_evidence(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    operation: str,
    scope_id: str,
    key_digest: str,
    request_digest: str,
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return new_evidence(
        organization_id=organization_id,
        scope=_scope(actor, operation=operation, scope_id=scope_id),
        identity=IdempotencyIdentity(key_digest, request_digest),
        result_kind=result_kind,
        result_ref=result_ref,
        now=now,
    )


def _scope(actor: AuthenticatedActor, *, operation: str, scope_id: str) -> EvidenceScope:
    return EvidenceScope(
        workspace_id=actor.workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        organization_id=actor.boundary_organization_id,
    )


__all__ = [
    "load_plugin_evidence",
    "new_plugin_evidence",
    "plugin_key_digest",
]
