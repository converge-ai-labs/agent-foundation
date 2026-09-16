"""Relational current candidates; conversation identity remains Session and Thread."""

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
from a13n_service.temporal import assume_utc

from .domain import ConfigurationApplicationReceipt, ConfigurationDraft


class ConfigurationDraftRecord(Base):
    __tablename__ = "configuration_drafts"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id"),
            ("sessions.organization_id", "sessions.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("target_agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("source_agent_revision_id", "organization_id", "workspace_id"),
            ("agent_revisions.id", "agent_revisions.organization_id", "agent_revisions.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("base_agent_revision_id", "organization_id", "workspace_id"),
            ("agent_revisions.id", "agent_revisions.organization_id", "agent_revisions.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status IN ('open', 'discarded', 'expired')", name="status_valid"),
        CheckConstraint("source_selector IN ('current', 'explicit', 'empty')", name="source_selector_valid"),
        CheckConstraint(
            "(source_selector = 'empty' AND source_agent_revision_id IS NULL "
            "AND source_agent_revision_version IS NULL) OR "
            "(source_selector <> 'empty' AND source_agent_revision_id IS NOT NULL "
            "AND source_agent_revision_version IS NOT NULL AND source_agent_revision_version >= 1)",
            name="source_shape_valid",
        ),
        CheckConstraint(
            "(mode = 'create' AND target_agent_id IS NULL AND source_selector = 'empty' "
            "AND base_agent_revision_id IS NULL AND base_agent_version IS NULL) OR "
            "(mode = 'update' AND target_agent_id IS NOT NULL AND base_agent_revision_id IS NOT NULL "
            "AND base_agent_version IS NOT NULL AND base_agent_version >= 1 AND config IS NOT NULL)",
            name="target_shape_valid",
        ),
        CheckConstraint("length(content_digest) = 64", name="digest_sha256"),
        UniqueConstraint("session_id", name="uq_configuration_drafts_session"),
        UniqueConstraint("id", "organization_id", "workspace_id", name="uq_configuration_drafts_scope"),
        UniqueConstraint("id", "organization_id", "session_id", name="uq_configuration_drafts_session_scope"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    target_agent_id: Mapped[str | None] = mapped_column(String(72))
    source_selector: Mapped[str] = mapped_column(String(16), nullable=False)
    source_agent_revision_id: Mapped[str | None] = mapped_column(String(72))
    source_agent_revision_version: Mapped[int | None] = mapped_column(BigInteger)
    base_agent_revision_id: Mapped[str | None] = mapped_column(String(72))
    base_agent_version: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    creation_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latest_validation: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    terminal_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ConfigurationDraft:
        values = {name: getattr(self, name) for name in ConfigurationDraft.model_fields}
        values["created_at"] = assume_utc(self.created_at)
        values["updated_at"] = assume_utc(self.updated_at)
        return ConfigurationDraft.model_validate(values)


class ConfigurationApplicationRecord(Base):
    """One immutable publication result for an exact reviewed draft version."""

    __tablename__ = "configuration_applications"
    __table_args__ = (
        ForeignKeyConstraint(
            ("draft_id", "organization_id", "workspace_id"),
            ("configuration_drafts.id", "configuration_drafts.organization_id", "configuration_drafts.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("agent_revision_id", "organization_id", "workspace_id"),
            ("agent_revisions.id", "agent_revisions.organization_id", "agent_revisions.workspace_id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("draft_id", "reviewed_version", name="uq_configuration_applications_version"),
        UniqueConstraint("draft_id", "key_hash", name="uq_configuration_applications_key"),
        CheckConstraint("reviewed_version >= 1", name="version_positive"),
        Index("ix_configuration_applications_draft_created", "draft_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(72), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    reviewed_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ConfigurationApplicationReceipt:
        return ConfigurationApplicationReceipt.model_validate(self.receipt)
