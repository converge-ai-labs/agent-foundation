"""HTTP mutation request identity and replay evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor
from a13n_service.public_errors import PublicError


def request_identity(idempotency_key: str, request: BaseModel) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise PublicError("idempotency_key_invalid", "Invalid Idempotency-Key.", status_code=400) from error
    request_digest = hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json", by_alias=True, exclude_unset=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return IdempotencyIdentity(key_digest, request_digest)


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> tuple[str, str] | None:
    try:
        evidence = await load_evidence(
            session,
            scope=EvidenceScope(
                workspace_id=actor.boundary_workspace_id,
                actor_type=actor.principal.principal_type.value,
                actor_id=actor.principal.principal_id,
                operation=operation,
                scope_id=scope_id,
            ),
            identity=identity,
            now=now,
        )
    except IdempotencyConflict as error:
        raise PublicError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            status_code=409,
        ) from error
    if evidence is None:
        return None
    return evidence.result_kind, evidence.result_ref


def evidence_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return new_evidence(
        organization_id=organization_id,
        scope=EvidenceScope(
            workspace_id=workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
        ),
        identity=identity,
        result_kind=result_kind,
        result_ref=result_ref,
        now=now,
    )
