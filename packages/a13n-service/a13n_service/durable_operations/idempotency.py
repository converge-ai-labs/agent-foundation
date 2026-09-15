"""Canonical idempotency-key and bounded replay-evidence primitives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import JsonValue
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service import digests
from a13n_service.ids import new_object_id
from a13n_service.temporal import assume_utc

from .models import IdempotencyEvidenceRecord

IDEMPOTENCY_EVIDENCE_TTL = timedelta(hours=24)
IDEMPOTENCY_KEY_MAX_BYTES = 512
_EVIDENCE_UNIQUE_CONSTRAINT = "uq_idempotency_evidence_replay_scope"


class InvalidIdempotencyKey(ValueError):
    """An idempotency key does not satisfy its feature's accepted byte policy."""


class IdempotencyConflict(Exception):
    """An unexpired key was reused for different request content."""


@dataclass(frozen=True, slots=True)
class IdempotencyIdentity:
    """Digests which identify one key and its canonical request content."""

    key_digest: str
    request_digest: str

    @classmethod
    def from_request(cls, key: str, request: object) -> IdempotencyIdentity:
        return cls(digest_visible_ascii_key(key), digests.digest_request(request))


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    """Durable replay scope shared by Service command families."""

    workspace_id: str | None
    actor_type: str
    actor_id: str
    operation: str
    scope_id: str
    organization_id: str | None = None


def digest_visible_ascii_key(value: str) -> str:
    """Digest a key containing 1 through 512 visible ASCII bytes."""

    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise InvalidIdempotencyKey from error
    if not 1 <= len(encoded) <= IDEMPOTENCY_KEY_MAX_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise InvalidIdempotencyKey
    return hashlib.sha256(encoded).hexdigest()


async def load_evidence(
    session: AsyncSession,
    *,
    scope: EvidenceScope,
    identity: IdempotencyIdentity,
    now: datetime,
) -> IdempotencyEvidenceRecord | None:
    """Load matching evidence, deleting it atomically once its TTL elapses."""

    boundary_id = scope.workspace_id or scope.organization_id
    if boundary_id is None:
        raise ValueError("Idempotency evidence requires a Workspace or Organization boundary")
    # Serialize a key even before its first row exists; expiry replacement
    # and the mutation use this same transaction and bounded DB timeouts.
    material = digests.digest_request(
        (boundary_id, scope.actor_type, scope.actor_id, scope.operation, scope.scope_id, identity.key_digest)
    )
    lock_id = int.from_bytes(bytes.fromhex(material)[:8], signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    evidence = await session.scalar(
        select(IdempotencyEvidenceRecord)
        .where(
            IdempotencyEvidenceRecord.boundary_scope_id == boundary_id,
            IdempotencyEvidenceRecord.actor_type == scope.actor_type,
            IdempotencyEvidenceRecord.actor_id == scope.actor_id,
            IdempotencyEvidenceRecord.operation == scope.operation,
            IdempotencyEvidenceRecord.scope_id == scope.scope_id,
            IdempotencyEvidenceRecord.key_digest == identity.key_digest,
        )
        .with_for_update()
    )
    if evidence is None:
        return None
    if assume_utc(evidence.expires_at) <= assume_utc(now):
        await session.delete(evidence)
        await session.flush()
        return None
    if evidence.request_digest != identity.request_digest:
        raise IdempotencyConflict
    return evidence


async def delete_expired_evidence(session: AsyncSession, *, now: datetime, limit: int) -> int:
    """Physically remove one bounded batch without waiting on active commands."""
    if not 1 <= limit <= 1000:
        raise ValueError("Evidence cleanup limit must be between 1 and 1000")
    records = (
        await session.scalars(
            select(IdempotencyEvidenceRecord)
            .where(IdempotencyEvidenceRecord.expires_at <= now)
            .order_by(IdempotencyEvidenceRecord.expires_at, IdempotencyEvidenceRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for record in records:
        await session.delete(record)
    await session.flush()
    return len(records)


def new_evidence(
    *,
    organization_id: str,
    scope: EvidenceScope,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
    receipt: dict[str, JsonValue] | None = None,
) -> IdempotencyEvidenceRecord:
    """Build one bounded replay-evidence row using the canonical lifetime."""

    return IdempotencyEvidenceRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=scope.workspace_id,
        boundary_scope_id=scope.workspace_id or organization_id,
        actor_type=scope.actor_type,
        actor_id=scope.actor_id,
        operation=scope.operation,
        scope_id=scope.scope_id,
        key_digest=identity.key_digest,
        request_digest=identity.request_digest,
        result_kind=result_kind,
        result_ref=result_ref,
        receipt_json=receipt,
        created_at=now,
        expires_at=now + IDEMPOTENCY_EVIDENCE_TTL,
    )


def is_evidence_unique_race(error: IntegrityError) -> bool:
    """Return whether an integrity error is the evidence replay-scope race."""

    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == _EVIDENCE_UNIQUE_CONSTRAINT
    message = str(getattr(error, "orig", error)).casefold()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message


__all__ = [
    "IDEMPOTENCY_EVIDENCE_TTL",
    "IDEMPOTENCY_KEY_MAX_BYTES",
    "EvidenceScope",
    "IdempotencyConflict",
    "IdempotencyIdentity",
    "InvalidIdempotencyKey",
    "delete_expired_evidence",
    "digest_visible_ascii_key",
    "is_evidence_unique_race",
    "load_evidence",
    "new_evidence",
]
