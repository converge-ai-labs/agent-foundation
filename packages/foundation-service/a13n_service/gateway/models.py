"""Durable protocol-adapter bindings owned by the Foundation Service Gateway."""

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

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base


class AguiThreadBindingRecord(Base):
    """One client-owned AG-UI thread mapped to an active Foundation Thread."""

    __tablename__ = "agui_thread_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id"),
            ("sessions.tenant_id", "sessions.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "root_thread_id"),
            ("threads.tenant_id", "threads.id"),
            name="fk_agui_thread_bindings_root_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "active_thread_id"),
            ("threads.tenant_id", "threads.id"),
            name="fk_agui_thread_bindings_active_thread",
            ondelete="RESTRICT",
        ),
        CheckConstraint("client_principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint(
            "client_principal_type",
            "client_principal_id",
            "agent_id",
            "external_thread_id",
            name="uq_agui_thread_bindings_client_agent_thread",
        ),
        Index(
            "uq_agui_thread_bindings_id_scope",
            "id",
            "organization_id",
            "workspace_id",
            unique=True,
        ),
        Index("ix_agui_thread_bindings_active_thread", "organization_id", "active_thread_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    client_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    client_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(512), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    root_thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    active_thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AguiRunBindingRecord(Base):
    """One external AG-UI run identity mapped to one Foundation Run."""

    __tablename__ = "agui_run_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("thread_binding_id", "organization_id", "workspace_id"),
            (
                "agui_thread_bindings.id",
                "agui_thread_bindings.organization_id",
                "agui_thread_bindings.workspace_id",
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("agent_revision_id", "organization_id", "workspace_id"),
            ("agent_revisions.id", "agent_revisions.organization_id", "agent_revisions.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(request_digest_sha256) = 64", name="request_digest_sha256"),
        UniqueConstraint("thread_binding_id", "external_run_id", name="uq_agui_run_bindings_external_run"),
        UniqueConstraint("organization_id", "run_id", name="uq_agui_run_bindings_foundation_run"),
        Index(
            "uq_agui_run_bindings_id_scope",
            "id",
            "organization_id",
            "workspace_id",
            unique=True,
        ),
        Index("ix_agui_run_bindings_foundation_run", "organization_id", "run_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_binding_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(512), nullable=False)
    parent_external_run_id: Mapped[str | None] = mapped_column(String(512))
    request_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class A2AContextBindingRecord(Base):
    """One A2A Context mapped to one Foundation Session root Thread."""

    __tablename__ = "a2a_context_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id"),
            ("sessions.tenant_id", "sessions.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "root_thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("client_principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        UniqueConstraint(
            "client_principal_type",
            "client_principal_id",
            "agent_id",
            "context_id",
            name="uq_a2a_context_client_agent_context",
        ),
        Index("uq_a2a_context_id_scope", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_a2a_context_root_thread", "organization_id", "root_thread_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    client_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    client_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    context_id: Mapped[str] = mapped_column(String(512), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    root_thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class A2ATaskBindingRecord(Base):
    """One A2A Task projected over ordered Runs in one Context Thread."""

    __tablename__ = "a2a_task_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("context_binding_id", "organization_id", "workspace_id"),
            (
                "a2a_context_bindings.id",
                "a2a_context_bindings.organization_id",
                "a2a_context_bindings.workspace_id",
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_revision_id", "organization_id", "workspace_id"),
            ("agent_revisions.id", "agent_revisions.organization_id", "agent_revisions.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "current_run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(client_tool_surface_digest) = 64", name="client_tool_surface_digest_sha256"),
        UniqueConstraint("organization_id", "id", name="uq_a2a_tasks_tenant_id"),
        Index("ix_a2a_tasks_context_created", "context_binding_id", "created_at", "id"),
        Index("ix_a2a_tasks_current_run", "organization_id", "current_run_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    context_binding_id: Mapped[str] = mapped_column(String(72), nullable=False)
    context_id: Mapped[str] = mapped_column(String(512), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    current_run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    client_tool_surface_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class A2AMessageBindingRecord(Base):
    """Immutable accepted A2A Message idempotency and Run correlation."""

    __tablename__ = "a2a_message_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("task_id", "organization_id"),
            ("a2a_task_bindings.id", "a2a_task_bindings.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("client_principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint("length(request_digest_sha256) = 64", name="request_digest_sha256"),
        UniqueConstraint(
            "client_principal_type",
            "client_principal_id",
            "agent_id",
            "message_id",
            name="uq_a2a_messages_client_agent_message",
        ),
        Index("ix_a2a_messages_run", "organization_id", "run_id", "id"),
        Index("ix_a2a_messages_task_history", "workspace_id", "task_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    client_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    client_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    task_id: Mapped[str] = mapped_column(String(72), nullable=False)
    message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    request_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class A2APushConfigurationRecord(ResourceCredential[str], Base):
    """One protected future-delivery configuration owned by an A2A Task."""

    credential_owner_type = "a2a_push_configuration"
    __tablename__ = "a2a_push_configurations"
    __table_args__ = (
        ForeignKeyConstraint(
            ("task_id", "organization_id"),
            ("a2a_task_bindings.id", "a2a_task_bindings.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("creator_principal_type IN ('user', 'service_account')", name="creator_type_valid"),
        CheckConstraint("state IN ('active', 'disabled')", name="state_valid"),
        CheckConstraint("delivery_generation >= 1", name="delivery_generation_positive"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        CheckConstraint(
            "(state = 'active' AND deleted_at IS NULL) OR (state = 'disabled' AND deleted_at IS NOT NULL)",
            name="lifecycle_valid",
        ),
        UniqueConstraint("organization_id", "id", name="uq_a2a_push_configurations_tenant_id"),
        Index(
            "ix_a2a_push_configurations_task",
            "organization_id",
            "task_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    task_id: Mapped[str] = mapped_column(String(72), nullable=False)
    creator_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    creator_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    authentication_scheme: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "A2AContextBindingRecord",
    "A2AMessageBindingRecord",
    "A2APushConfigurationRecord",
    "A2ATaskBindingRecord",
    "AguiRunBindingRecord",
    "AguiThreadBindingRecord",
]
