"""Relational heads for accepted generations, Threads, child executions, and Environment state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .metadata import Base
from .utc_datetime import UtcDateTime

_DIGEST = 64
_ID = 128


class AcceptedConfigurationRecord(Base):
    __tablename__ = "accepted_configuration"

    generation_digest: Mapped[str] = mapped_column(String(_DIGEST), primary_key=True)
    object_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    object_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class CurrentConfigurationRecord(Base):
    __tablename__ = "current_configuration"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_digest: Mapped[str] = mapped_column(
        String(_DIGEST),
        ForeignKey("accepted_configuration.generation_digest", ondelete="RESTRICT"),
        nullable=False,
    )


class ConfigurationSourceRecord(Base):
    __tablename__ = "configuration_source"

    generation_digest: Mapped[str] = mapped_column(
        String(_DIGEST),
        ForeignKey("accepted_configuration.generation_digest", ondelete="CASCADE"),
        primary_key=True,
    )
    relative_path: Mapped[str] = mapped_column(Text, primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(_ID), nullable=True)


class ResourceIndexRecord(Base):
    __tablename__ = "resource_index"
    __table_args__ = (UniqueConstraint("generation_digest", "resource_kind", "resource_id", name="resource_identity"),)

    generation_digest: Mapped[str] = mapped_column(
        String(_DIGEST),
        ForeignKey("accepted_configuration.generation_digest", ondelete="CASCADE"),
        primary_key=True,
    )
    relative_path: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    name: Mapped[str] = mapped_column("display_name", String(256), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    normalized_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)


class ThreadRecord(Base):
    __tablename__ = "thread"

    thread_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_thread_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=True, index=True
    )
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    initial_state_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_state_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    continuation_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    continuation_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)


class ThreadConfigurationRecord(Base):
    __tablename__ = "thread_configuration"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("agent_source_kind IN ('agent', 'markdown')", name="agent_source_kind"),
    )

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    agent_source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_source_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    environment_profile_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    harness_plugin_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    environment_run_extension_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    mcp_server_ids_json: Mapped[str] = mapped_column(Text, nullable=False)


class ChildExecutionRecord(Base):
    __tablename__ = "child_execution"
    __table_args__ = (
        CheckConstraint("segment_index >= 0", name="segment_index"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed', 'cancelled', 'lost')", name="status"),
        CheckConstraint(
            "(segment_index = 0 AND resumed_from IS NULL) OR (segment_index > 0 AND resumed_from IS NOT NULL)",
            name="resume_link",
        ),
        UniqueConstraint("child_thread_id", "segment_index", name="segment"),
    )

    execution_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    run_composition_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    run_composition_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    selected_checkpoint_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_checkpoint_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    resumed_from: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("child_execution.execution_id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class EnvironmentBindingRecord(Base):
    __tablename__ = "environment_binding"
    __table_args__ = (
        CheckConstraint(
            "(state_digest IS NULL AND state_schema_version IS NULL) OR "
            "(state_digest IS NOT NULL AND state_schema_version IS NOT NULL)",
            name="state_reference",
        ),
    )

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    environment_profile_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    profile_digest: Mapped[str] = mapped_column(String(_DIGEST), primary_key=True)
    adapter_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    normalized_root: Mapped[str] = mapped_column(Text, primary_key=True)
    state_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


__all__ = [
    "AcceptedConfigurationRecord",
    "ChildExecutionRecord",
    "ConfigurationSourceRecord",
    "CurrentConfigurationRecord",
    "EnvironmentBindingRecord",
    "ResourceIndexRecord",
    "ThreadConfigurationRecord",
    "ThreadRecord",
]
