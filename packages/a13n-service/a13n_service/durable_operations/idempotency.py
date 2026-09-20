"""Scoped key-to-result references for repeatable mutations of existing resources."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import Select, Table, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service import digests
from a13n_service.ids import new_object_id

from .models import IdempotencyEvidenceRecord

IDEMPOTENCY_KEY_MAX_BYTES = 512
_EVIDENCE_UNIQUE_CONSTRAINT = "uq_idempotency_evidence_replay_scope"


class InvalidIdempotencyKey(ValueError):
    """An idempotency key does not satisfy its feature's accepted byte policy."""


class EvidenceAlreadyCommitted(Exception):
    """A live receipt won; roll back tentative mutations before replaying it."""


@dataclass(frozen=True, slots=True)
class IdempotencyIdentity:
    """The validated digest of an opaque caller key."""

    key_digest: str

    @classmethod
    def from_key(cls, key: str) -> IdempotencyIdentity:
        return cls(digest_visible_ascii_key(key))


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
    """Serialize a mutation key and load its result reference."""

    boundary_id = scope.workspace_id or scope.organization_id
    if boundary_id is None:
        raise ValueError("Idempotency evidence requires a Workspace or Organization boundary")
    # Serialize a key even before its first row exists; key lookup
    # and the mutation use this same transaction and bounded DB timeouts.
    material = digests.digest_request(
        (boundary_id, scope.actor_type, scope.actor_id, scope.operation, scope.scope_id, identity.key_digest)
    )
    lock_id = int.from_bytes(bytes.fromhex(material)[:8], signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    evidence = await session.scalar(_evidence_query(scope, identity).with_for_update())
    if evidence is None:
        return None
    return evidence


async def find_evidence(
    session: AsyncSession,
    *,
    scope: EvidenceScope,
    identity: IdempotencyIdentity,
    now: datetime,
) -> IdempotencyEvidenceRecord | None:
    """Read a key-to-result reference without locking."""

    evidence = await session.scalar(_evidence_query(scope, identity))
    return evidence


async def insert_evidence(session: AsyncSession, evidence: IdempotencyEvidenceRecord) -> None:
    """Arbitrate key insertion in the business transaction.

    An existing winner is never overwritten. The
    caller must roll back its tentative business writes before reading that winner.
    """

    table = cast(Table, IdempotencyEvidenceRecord.__table__)
    statement = insert(table).values({column.name: getattr(evidence, column.name) for column in table.columns})
    statement = statement.on_conflict_do_nothing(constraint=_EVIDENCE_UNIQUE_CONSTRAINT).returning(table.c.id)
    if await session.scalar(statement) is None:
        raise EvidenceAlreadyCommitted


def _evidence_query(scope: EvidenceScope, identity: IdempotencyIdentity) -> Select[tuple[IdempotencyEvidenceRecord]]:
    boundary_id = scope.workspace_id or scope.organization_id
    if boundary_id is None:
        raise ValueError("Idempotency evidence requires a Workspace or Organization boundary")
    return select(IdempotencyEvidenceRecord).where(
        IdempotencyEvidenceRecord.boundary_scope_id == boundary_id,
        IdempotencyEvidenceRecord.actor_type == scope.actor_type,
        IdempotencyEvidenceRecord.actor_id == scope.actor_id,
        IdempotencyEvidenceRecord.operation == scope.operation,
        IdempotencyEvidenceRecord.scope_id == scope.scope_id,
        IdempotencyEvidenceRecord.key_digest == identity.key_digest,
    )


def new_evidence(
    *,
    organization_id: str,
    scope: EvidenceScope,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    """Build a key-to-result reference with no response snapshot or expiry."""

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
        result_kind=result_kind,
        result_ref=result_ref,
        created_at=now,
    )


def is_evidence_unique_race(error: IntegrityError) -> bool:
    """Return whether an integrity error is the evidence replay-scope race."""

    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == _EVIDENCE_UNIQUE_CONSTRAINT
    message = str(getattr(error, "orig", error)).casefold()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message


__all__ = [
    "IDEMPOTENCY_KEY_MAX_BYTES",
    "EvidenceAlreadyCommitted",
    "EvidenceScope",
    "IdempotencyIdentity",
    "InvalidIdempotencyKey",
    "digest_visible_ascii_key",
    "find_evidence",
    "insert_evidence",
    "is_evidence_unique_race",
    "load_evidence",
    "new_evidence",
]
