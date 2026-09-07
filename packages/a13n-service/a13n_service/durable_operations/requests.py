"""HTTP mutation request identity and replay evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_request,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam import AuthenticatedActor


@dataclass(frozen=True, slots=True)
class ReplayReceipt:
    result_kind: str
    result_ref: str
    response: dict[str, JsonValue]

    def restore[T: BaseModel](self, model: type[T]) -> T:
        return model.model_validate(self.response)


def request_identity(idempotency_key: str, request: object) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise ApplicationError(
            "idempotency_key_invalid", "Invalid Idempotency-Key.", category=ErrorCategory.invalid_request
        ) from error
    digest = digest_request(request)
    return IdempotencyIdentity(key_digest, digest)


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> ReplayReceipt | None:
    try:
        evidence = await load_evidence(
            session,
            scope=EvidenceScope(
                workspace_id=actor.boundary_workspace_id,
                actor_type=actor.principal.principal_type.value,
                actor_id=actor.principal.principal_id,
                operation=operation,
                scope_id=scope_id,
                organization_id=actor.boundary_organization_id,
            ),
            identity=identity,
            now=now,
        )
    except IdempotencyConflict as error:
        raise ApplicationError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            category=ErrorCategory.conflict,
        ) from error
    if evidence is None:
        return None
    if evidence.receipt_json is None:
        raise RuntimeError("resource command evidence has no original receipt")
    return ReplayReceipt(evidence.result_kind, evidence.result_ref, evidence.receipt_json)


def evidence_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
    response: BaseModel,
) -> IdempotencyEvidenceRecord:
    return new_evidence(
        organization_id=organization_id,
        scope=EvidenceScope(
            workspace_id=workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
            organization_id=actor.boundary_organization_id,
        ),
        identity=identity,
        result_kind=result_kind,
        result_ref=result_ref,
        receipt=response.model_dump(mode="json", by_alias=True),
        now=now,
    )
