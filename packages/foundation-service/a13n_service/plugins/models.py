"""Relational authority for Plugin identities and immutable Wheels."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base

from .domain import Plugin, PluginLifecycleState, PluginSource, PluginVersion


class PluginRecord(Base):
    __tablename__ = "plugins"
    __table_args__ = (
        CheckConstraint("source IN ('builtin', 'uploaded')", name="source_valid"),
        CheckConstraint("lifecycle_state IN ('available', 'archived')", name="lifecycle_state_valid"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        UniqueConstraint("plugin_key", name="uq_plugins_plugin_key"),
        UniqueConstraint("distribution_name", name="uq_plugins_distribution_name"),
        UniqueConstraint("top_level_package", name="uq_plugins_top_level_package"),
        ForeignKeyConstraint(
            ("active_version_id", "id"),
            ("plugin_versions.id", "plugin_versions.plugin_id"),
            name="fk_plugins_active_version_plugin_versions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index("ix_plugins_listing", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    plugin_key: Mapped[str] = mapped_column(String(128), nullable=False)
    distribution_name: Mapped[str] = mapped_column(String(256), nullable=False)
    top_level_package: Mapped[str] = mapped_column(String(256), nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(72))
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Plugin:
        return Plugin(
            id=self.id,
            source=PluginSource(self.source),
            plugin_key=self.plugin_key,
            distribution_name=self.distribution_name,
            top_level_package=self.top_level_package,
            active_version_id=self.active_version_id,
            lifecycle_state=PluginLifecycleState(self.lifecycle_state),
        )


class PluginVersionRecord(Base):
    __tablename__ = "plugin_versions"
    __table_args__ = (
        ForeignKeyConstraint(("plugin_id",), ("plugins.id",), ondelete="RESTRICT", use_alter=True),
        CheckConstraint("status = 'ready'", name="status_ready"),
        CheckConstraint("length(content_digest) = 64", name="content_digest_sha256"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        UniqueConstraint("plugin_id", "version", name="uq_plugin_versions_plugin_version"),
        UniqueConstraint("id", "plugin_id", name="uq_plugin_versions_id_plugin"),
        Index("ix_plugin_versions_listing", "plugin_id", "created_at", "id"),
        Index("ix_plugin_versions_digest", "content_digest"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[str] = mapped_column(String(256), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requires_dist: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    requires_python: Mapped[str | None] = mapped_column(String(1024))
    wheel_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    root_is_purelib: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_point_target: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> PluginVersion:
        return PluginVersion(
            id=self.id,
            plugin_id=self.plugin_id,
            version=self.version,
            content_digest=self.content_digest,
            artifact_ref=self.artifact_ref,
            requires_dist=tuple(self.requires_dist),
            status="ready",
        )


class PluginRuntimeStateRecord(Base):
    __tablename__ = "plugin_runtime_state"
    __table_args__ = (
        CheckConstraint("id = 'runtime'", name="singleton"),
        CheckConstraint("mode IN ('on_demand', 'runner')", name="mode_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active_lock_digest: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
