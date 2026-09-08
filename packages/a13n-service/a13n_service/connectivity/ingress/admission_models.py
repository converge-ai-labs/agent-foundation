"""External conversation bindings, ordered batches, and deduplicated events."""

from datetime import datetime
from typing import Any

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


class AgentThreadBindingRecord(Base):
    __tablename__ = "agent_thread_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("organization_id", "thread_id"), ("threads.organization_id", "threads.id"), ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "account_id", "external_ref_kind", "external_ref_id", name="uq_agent_thread_bindings_external_ref"
        ),
        CheckConstraint("next_batch_sequence >= 1", name="sequence_positive"),
        Index("uq_agent_thread_bindings_id_organization", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_agent_thread_bindings_thread", "organization_id", "thread_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_id: Mapped[str] = mapped_column(String(72), nullable=False)
    external_ref_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    external_ref_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(72))
    next_batch_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    next_submission_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngressBatchRecord(Base):
    __tablename__ = "ingress_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ("binding_id", "organization_id", "workspace_id"),
            ("agent_thread_bindings.id", "agent_thread_bindings.organization_id", "agent_thread_bindings.workspace_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("binding_id", "sequence", name="uq_ingress_batches_sequence"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status_valid"),
        CheckConstraint("event_count >= 1", name="event_count_positive"),
        CheckConstraint("event_bytes >= 0", name="event_bytes_non_negative"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("uq_ingress_batches_id_organization", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_ingress_batches_claim", "status", "available_at", "claim_expires_at", "id"),
        Index("ix_ingress_batches_retention", "status", "terminal_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    binding_id: Mapped[str] = mapped_column(String(72), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    append_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_kind: Mapped[str | None] = mapped_column(String(32))
    result_id: Mapped[str | None] = mapped_column(String(72))
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngressAdmissionRecord(Base):
    __tablename__ = "ingress_admissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("batch_id", "organization_id", "workspace_id"),
            ("ingress_batches.id", "ingress_batches.organization_id", "ingress_batches.workspace_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "account_id", "event_identity_kind", "event_identity_digest", name="uq_ingress_admissions_identity"
        ),
        CheckConstraint("event_size_bytes >= 0", name="event_size_non_negative"),
        CheckConstraint("batch_id IS NOT NULL OR rejection_reason IS NOT NULL", name="event_destination_known"),
        Index("ix_ingress_admissions_order", "batch_id", "ordering_key", "id"),
        Index("ix_ingress_admissions_retention", "dedup_expires_at", "id"),
        Index("ix_ingress_admissions_capacity", "workspace_id", "account_id", "batch_id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_id: Mapped[str] = mapped_column(String(72), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(72))
    event_identity_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    event_identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ordering_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    event_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    dedup_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
