"""Relational indexes for Agent UI configuration, Sessions, and Environment state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .metadata import Base


class ConfigurationGenerationRecord(Base):
    """One completely accepted configuration generation."""

    __tablename__ = "configuration_generation"

    generation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    process_settings_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    process_settings_json: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    restart_required: Mapped[bool] = mapped_column(Boolean, nullable=False)


class CurrentConfigurationRecord(Base):
    """The latest accepted configuration generation."""

    __tablename__ = "current_configuration"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("configuration_generation.generation_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )


class ResourceRevisionRecord(Base):
    """Index from one resource revision to its content-addressed object."""

    __tablename__ = "resource_revision"
    __table_args__ = (UniqueConstraint("resource_kind", "resource_id", "content_digest", name="identity"),)

    revision_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    object_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationResourceRecord(Base):
    """Ordered complete resource index for one accepted generation."""

    __tablename__ = "generation_resource"
    __table_args__ = (
        ForeignKeyConstraint(
            ("resource_kind", "resource_id", "content_digest"),
            (
                "resource_revision.resource_kind",
                "resource_revision.resource_id",
                "resource_revision.content_digest",
            ),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("generation_id", "resource_kind", "resource_id", name="resource_identity"),
    )

    generation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("configuration_generation.generation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class SkillPackageReferenceRecord(Base):
    """Package-object selection for one managed Skill revision."""

    __tablename__ = "skill_package_reference"
    __table_args__ = (
        ForeignKeyConstraint(
            ("resource_kind", "resource_id", "content_digest"),
            (
                "resource_revision.resource_kind",
                "resource_revision.resource_id",
                "resource_revision.content_digest",
            ),
            ondelete="CASCADE",
        ),
        CheckConstraint("resource_kind = 'skill'", name="skill_kind"),
    )

    resource_kind: Mapped[str] = mapped_column(String(32), primary_key=True, default="skill")
    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    content_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class CompositionSnapshotRecord(Base):
    """Index for one immutable Agent or Environment snapshot."""

    __tablename__ = "composition_snapshot"
    __table_args__ = (
        ForeignKeyConstraint(
            ("root_resource_kind", "root_resource_id", "root_content_digest"),
            (
                "resource_revision.resource_kind",
                "resource_revision.resource_id",
                "resource_revision.content_digest",
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint("snapshot_kind IN ('agent', 'environment')", name="snapshot_kind"),
        UniqueConstraint("snapshot_kind", "logical_digest", name="identity"),
    )

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    logical_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    object_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    generation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("configuration_generation.generation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    root_resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    root_resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    root_content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigurationDiagnosticRecord(Base):
    """Bounded path-free evidence from configuration reload attempts."""

    __tablename__ = "configuration_diagnostic"

    diagnostic_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRecord(Base):
    """Session metadata, pinned composition, and latest continuation reference."""

    __tablename__ = "local_session"

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    agent_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("composition_snapshot.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    environment_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("composition_snapshot.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    skill_selections_json: Mapped[str] = mapped_column(Text, nullable=False)
    parent_fork_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    continuation_object_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class SessionEnvironmentResourceRecord(Base):
    """Latest provider state for one Session Environment mount."""

    __tablename__ = "session_environment_resource"
    __table_args__ = (
        CheckConstraint(
            "status IN ('unprovisioned', 'available', 'paused', 'unavailable')",
            name="status",
        ),
        CheckConstraint(
            "resource_allocation IN ('single_from_spec', 'multiple_from_spec')",
            name="resource_allocation",
        ),
    )

    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    mount_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_alias: Mapped[str] = mapped_column(String(63), nullable=False)
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_spec_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    resource_allocation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CompositionSnapshotRecord",
    "ConfigurationDiagnosticRecord",
    "ConfigurationGenerationRecord",
    "CurrentConfigurationRecord",
    "GenerationResourceRecord",
    "ResourceRevisionRecord",
    "SessionEnvironmentResourceRecord",
    "SessionRecord",
    "SkillPackageReferenceRecord",
]
