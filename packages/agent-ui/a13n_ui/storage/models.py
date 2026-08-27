"""Foundation tables for Agent UI local-store ownership and object registration."""

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


class StoreLeaseRecord(Base):
    """The process generation currently owning this data root."""

    __tablename__ = "store_lease"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImmutableObjectRecord(Base):
    """One verified immutable object known to the metadata store."""

    __tablename__ = "immutable_object"

    logical_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryDiagnosticRecord(Base):
    """Bounded evidence from local-store recovery and quarantine."""

    __tablename__ = "recovery_diagnostic"

    diagnostic_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    """The one atomically selected latest accepted generation."""

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
    """Durable index from semantic resource revision to its immutable object."""

    __tablename__ = "resource_revision"
    __table_args__ = (UniqueConstraint("resource_kind", "resource_id", "content_digest", name="identity"),)

    revision_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    object_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
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
    """Durable package-object selection for one managed Skill revision."""

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
    object_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=False,
    )


class CompositionSnapshotRecord(Base):
    """Durable selection of one verified immutable composition snapshot."""

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
    object_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
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
    """Bounded path-free evidence from accepted or rejected reload attempts."""

    __tablename__ = "configuration_diagnostic"

    diagnostic_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CompositionSnapshotRecord",
    "ConfigurationDiagnosticRecord",
    "ConfigurationGenerationRecord",
    "CurrentConfigurationRecord",
    "GenerationResourceRecord",
    "ImmutableObjectRecord",
    "RecoveryDiagnosticRecord",
    "ResourceRevisionRecord",
    "SkillPackageReferenceRecord",
    "StoreLeaseRecord",
]
