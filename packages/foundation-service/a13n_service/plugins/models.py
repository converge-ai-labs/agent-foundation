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
        CheckConstraint("command_claim_generation >= 0", name="command_claim_generation_non_negative"),
        CheckConstraint(
            "(command_operation_id IS NULL AND command_lease_expires_at IS NULL) OR "
            "(command_operation_id IS NOT NULL AND command_lease_expires_at IS NOT NULL)",
            name="command_lease_shape_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    active_lock_digest: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_operation_id: Mapped[str | None] = mapped_column(String(72))
    command_claim_generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    command_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PluginRuntimeLockRecord(Base):
    __tablename__ = "plugin_runtime_locks"
    __table_args__ = (
        CheckConstraint("length(digest) = 64", name="digest_sha256"),
        CheckConstraint("schema_version = '1'", name="schema_version_v1"),
        CheckConstraint("mode IN ('on_demand', 'runner')", name="mode_valid"),
        Index("ix_plugin_runtime_locks_created", "created_at", "digest"),
    )

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PluginRuntimeTaskRecord(Base):
    __tablename__ = "plugin_runtime_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(("plugin_id",), ("plugins.id",), ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ("plugin_version_id", "plugin_id"),
            ("plugin_versions.id", "plugin_versions.plugin_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("candidate_lock_digest",),
            ("plugin_runtime_locks.digest",),
            ondelete="RESTRICT",
        ),
        CheckConstraint("actor_type IN ('user', 'service_account')", name="actor_type_valid"),
        CheckConstraint("command IN ('activate', 'deactivate')", name="command_valid"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),
        CheckConstraint(
            "phase IN ('accepted', 'candidate_ready', 'staged', 'committed', 'succeeded', 'failed')",
            name="phase_valid",
        ),
        CheckConstraint(
            "(command = 'activate' AND plugin_version_id IS NOT NULL) OR "
            "(command = 'deactivate' AND plugin_version_id IS NULL)",
            name="target_shape_valid",
        ),
        CheckConstraint(
            "(status = 'running' AND phase IN ('accepted', 'candidate_ready', 'staged', 'committed') "
            "AND completed_at IS NULL) OR "
            "(status = 'succeeded' AND phase = 'succeeded' AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND phase = 'failed' AND completed_at IS NOT NULL)",
            name="terminal_shape_valid",
        ),
        Index("ix_plugin_runtime_tasks_reconcile", "status", "created_at", "id"),
        Index("ix_plugin_runtime_tasks_actor", "actor_type", "actor_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(72), nullable=False)
    command: Mapped[str] = mapped_column(String(16), nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(72), nullable=False)
    plugin_version_id: Mapped[str | None] = mapped_column(String(72))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_runtime_version: Mapped[int | None] = mapped_column(BigInteger)
    candidate_lock_digest: Mapped[str | None] = mapped_column(String(64))
    staging_token: Mapped[str | None] = mapped_column(String(512))
    committed_runtime_version: Mapped[int | None] = mapped_column(BigInteger)
    result_refs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    error: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
