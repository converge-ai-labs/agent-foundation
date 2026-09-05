"""Shared idempotency evidence and outbox records."""

from __future__ import annotations

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class IdempotencyEvidenceRecord(Base):
    """Bounded replay evidence shared by retry-safe Foundation commands."""

    __tablename__ = "idempotency_evidence"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "boundary_scope_id",
            "actor_type",
            "actor_id",
            "operation",
            "scope_id",
            "key_digest",
            name="uq_idempotency_evidence_replay_scope",
        ),
        CheckConstraint("boundary_scope_id = coalesce(workspace_id, organization_id)", name="boundary_scope_valid"),
        CheckConstraint("actor_type IN ('user', 'service_account')", name="actor_type_valid"),
        CheckConstraint("length(key_digest) = 64", name="key_digest_sha256"),
        CheckConstraint("length(request_digest) = 64", name="request_digest_sha256"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_idempotency_evidence_expiry", "expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    boundary_scope_id: Mapped[str] = mapped_column(String(72))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(72))
    operation: Mapped[str] = mapped_column(String(64))
    scope_id: Mapped[str] = mapped_column(String(72))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    result_kind: Mapped[str] = mapped_column(String(32))
    result_ref: Mapped[str] = mapped_column(String(72))
    receipt_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxRecord(Base):
    """One durable, fenced publication or cleanup intent."""

    __tablename__ = "outbox_records"
    __table_args__ = (
        UniqueConstraint(
            "source_kind",
            "source_id",
            "destination_kind",
            "destination_ref",
            name="uq_outbox_records_destination",
        ),
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'dead_lettered')",
            name="status_valid",
        ),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "(status = 'publishing' AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'publishing' AND lease_expires_at IS NULL)",
            name="lease_shape_valid",
        ),
        CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) OR (status <> 'published' AND published_at IS NULL)",
            name="published_shape_valid",
        ),
        CheckConstraint(
            "(status = 'dead_lettered' AND dead_lettered_at IS NOT NULL) OR "
            "(status <> 'dead_lettered' AND dead_lettered_at IS NULL)",
            name="dead_letter_shape_valid",
        ),
        Index("ix_outbox_records_due", "status", "available_at", "id"),
        Index("ix_outbox_records_destination", "destination_kind", "destination_ref", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(72))
    destination_kind: Mapped[str] = mapped_column(String(64))
    destination_ref: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claim_generation: Mapped[int] = mapped_column(BigInteger)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
