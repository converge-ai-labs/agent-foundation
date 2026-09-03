"""Relational authority for Ingress admissions, batches, and bindings."""

from __future__ import annotations

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
            ("ingress_id", "organization_id", "workspace_id"),
            ("ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint(
            "ingress_id", "external_ref_kind", "external_ref_id", name="uq_agent_thread_bindings_external_ref"
        ),
        Index("uq_agent_thread_bindings_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_agent_thread_bindings_thread", "organization_id", "thread_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ingress_id: Mapped[str] = mapped_column(String(72), nullable=False)
    external_ref_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    external_ref_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngressAdmissionRecord(Base):
    __tablename__ = "ingress_admissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("ingress_id", "organization_id", "workspace_id"),
            ("ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("route_id", "organization_id", "workspace_id"),
            ("ingress_routes.id", "ingress_routes.organization_id", "ingress_routes.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("selected_agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("binding_id", "organization_id", "workspace_id"),
            ("agent_thread_bindings.id", "agent_thread_bindings.organization_id", "agent_thread_bindings.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status_valid"),
        CheckConstraint("binding_state IN ('bound', 'unbound')", name="binding_state_valid"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        CheckConstraint("event_size_bytes >= 0", name="event_size_non_negative"),
        CheckConstraint("min_interval_ms >= 1", name="min_interval_positive"),
        CheckConstraint("max_batch_events >= 1", name="max_batch_events_positive"),
        UniqueConstraint(
            "ingress_id", "event_identity_kind", "event_identity_digest", name="uq_ingress_admissions_identity"
        ),
        Index("uq_ingress_admissions_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_ingress_admissions_pending", "status", "available_at", "id"),
        Index("ix_ingress_admissions_capacity", "workspace_id", "ingress_id", "status", "event_size_bytes"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ingress_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ingress_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    route_id: Mapped[str | None] = mapped_column(String(72))
    route_version: Mapped[int | None] = mapped_column(BigInteger)
    event_identity_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    event_identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    protected_event_identity: Mapped[str] = mapped_column(String(2048), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(128), nullable=False)
    event_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ordering_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    raw_ref_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    selected_agent_id: Mapped[str | None] = mapped_column(String(72))
    external_ref_kind: Mapped[str | None] = mapped_column(String(128))
    external_ref_id: Mapped[str | None] = mapped_column(String(2048))
    binding_state: Mapped[str] = mapped_column(String(16), nullable=False)
    binding_id: Mapped[str | None] = mapped_column(String(72))
    mapping_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    mapping_digest: Mapped[str | None] = mapped_column(String(64))
    min_interval_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_batch_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    native_actions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capability_overlay_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    compatibility_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_kind: Mapped[str | None] = mapped_column(String(32))
    result_id: Mapped[str | None] = mapped_column(String(72))
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedup_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngressBatchRecord(Base):
    __tablename__ = "ingress_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ("ingress_id", "organization_id", "workspace_id"),
            ("ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status_valid"),
        CheckConstraint("event_count >= 1", name="event_count_positive"),
        CheckConstraint("event_bytes >= 0", name="event_bytes_non_negative"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("uq_ingress_batches_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_ingress_batches_claim", "status", "available_at", "claim_expires_at", "id"),
        Index("ix_ingress_batches_compatible", "ingress_id", "compatibility_digest", "status", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ingress_id: Mapped[str] = mapped_column(String(72), nullable=False)
    compatibility_digest: Mapped[str] = mapped_column(String(64), nullable=False)
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


class IngressBatchEventRecord(Base):
    __tablename__ = "ingress_batch_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ("batch_id", "organization_id", "workspace_id"),
            ("ingress_batches.id", "ingress_batches.organization_id", "ingress_batches.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("admission_id", "organization_id", "workspace_id"),
            ("ingress_admissions.id", "ingress_admissions.organization_id", "ingress_admissions.workspace_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("admission_id", name="uq_ingress_batch_events_admission"),
        Index("ix_ingress_batch_events_order", "batch_id", "ordering_key", "admission_id"),
    )

    batch_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    admission_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ordering_key: Mapped[str] = mapped_column(String(2048), nullable=False)
