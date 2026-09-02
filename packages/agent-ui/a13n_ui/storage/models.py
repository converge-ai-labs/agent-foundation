"""Relational heads for accepted configuration, Sessions, child executions, and Environment state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .metadata import Base
from .utc_datetime import UtcDateTime

_DIGEST_LENGTH = 64
_SCHEMA_VERSION_LENGTH = 64
_SESSION_ID_LENGTH = 80
_THREAD_ID_LENGTH = 80
_RUN_ID_LENGTH = 80


class AcceptedConfigurationRecord(Base):
    """One completely validated source set retained for snapshot pinning."""

    __tablename__ = "accepted_configuration"

    source_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), primary_key=True)
    yaml_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class CurrentConfigurationRecord(Base):
    """The currently selectable accepted configuration."""

    __tablename__ = "current_configuration"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH),
        ForeignKey("accepted_configuration.source_digest", ondelete="RESTRICT"),
        nullable=False,
    )


class CompositionSnapshotRecord(Base):
    """Index for one immutable resolved Agent or Environment-profile snapshot."""

    __tablename__ = "composition_snapshot"
    __table_args__ = (CheckConstraint("snapshot_kind IN ('agent', 'environment')", name="snapshot_kind"),)

    logical_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), primary_key=True)
    snapshot_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    object_schema_version: Mapped[str] = mapped_column(String(_SCHEMA_VERSION_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class ConfigurationSnapshotRecord(Base):
    """Named exact snapshot selected by one accepted configuration."""

    __tablename__ = "configuration_snapshot"
    __table_args__ = (CheckConstraint("snapshot_kind IN ('agent', 'environment')", name="snapshot_kind"),)

    source_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH),
        ForeignKey("accepted_configuration.source_digest", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    logical_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH),
        ForeignKey("composition_snapshot.logical_digest", ondelete="RESTRICT"),
        nullable=False,
    )


class SessionRecord(Base):
    """Session metadata, pinned composition, and selected root continuation."""

    __tablename__ = "local_session"

    session_id: Mapped[str] = mapped_column(String(_SESSION_ID_LENGTH), primary_key=True)
    root_thread_id: Mapped[str] = mapped_column(String(_THREAD_ID_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    agent_snapshot_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH),
        ForeignKey("composition_snapshot.logical_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    environment_snapshot_digest: Mapped[str] = mapped_column(
        String(_DIGEST_LENGTH),
        ForeignKey("composition_snapshot.logical_digest", ondelete="RESTRICT"),
        nullable=False,
    )
    continuation_schema_version: Mapped[str] = mapped_column(String(_SCHEMA_VERSION_LENGTH), nullable=False)
    continuation_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), nullable=False)


class ChildThreadRecord(Base):
    """One logical persisted async child beneath a root or child Thread."""

    __tablename__ = "child_thread"

    child_thread_id: Mapped[str] = mapped_column(String(_THREAD_ID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(_SESSION_ID_LENGTH),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_thread_id: Mapped[str] = mapped_column(String(_THREAD_ID_LENGTH), nullable=False, index=True)
    subagent_name: Mapped[str] = mapped_column(String(63), nullable=False)
    child_definition_id: Mapped[str] = mapped_column(String(256), nullable=False)
    child_definition_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class ChildExecutionRecord(Base):
    """Mutable head for one contiguous execution segment of a child Thread."""

    __tablename__ = "child_execution"
    __table_args__ = (
        CheckConstraint("segment_index >= 0", name="segment_index"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'lost')",
            name="status",
        ),
        CheckConstraint(
            "(segment_index = 0 AND resumed_from IS NULL) OR (segment_index > 0 AND resumed_from IS NOT NULL)",
            name="resume_link",
        ),
        CheckConstraint(
            "(selected_checkpoint_digest IS NULL AND selected_checkpoint_schema_version IS NULL "
            "AND selected_checkpoint_terminal = 0 AND resumable = 0) OR "
            "(selected_checkpoint_digest IS NOT NULL AND selected_checkpoint_schema_version IS NOT NULL)",
            name="checkpoint_fields",
        ),
        CheckConstraint(
            "status != 'succeeded' OR (selected_checkpoint_digest IS NOT NULL AND selected_checkpoint_terminal = 1)",
            name="success_checkpoint",
        ),
        CheckConstraint(
            "resumable = 0 OR (status = 'succeeded' AND selected_checkpoint_terminal = 1)",
            name="resumable_terminal_success",
        ),
        UniqueConstraint("child_thread_id", "segment_index", name="segment"),
    )

    execution_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(_SESSION_ID_LENGTH),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_thread_id: Mapped[str] = mapped_column(
        String(_THREAD_ID_LENGTH),
        ForeignKey("child_thread.child_thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_run_id: Mapped[str] = mapped_column(String(_RUN_ID_LENGTH), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    selected_checkpoint_schema_version: Mapped[str | None] = mapped_column(
        String(_SCHEMA_VERSION_LENGTH), nullable=True
    )
    selected_checkpoint_digest: Mapped[str | None] = mapped_column(String(_DIGEST_LENGTH), nullable=True)
    selected_checkpoint_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resumable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resumed_from: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("child_execution.execution_id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class EnvironmentBindingRecord(Base):
    """Host-authoritative state for one complete folder binding key."""

    __tablename__ = "environment_binding"
    __table_args__ = (
        ForeignKeyConstraint(
            ("session_id",),
            ("local_session.session_id",),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(state_digest IS NULL AND state_schema_version IS NULL) OR "
            "(state_digest IS NOT NULL AND state_schema_version IS NOT NULL)",
            name="state_reference",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(_SESSION_ID_LENGTH), primary_key=True)
    profile_digest: Mapped[str] = mapped_column(String(_DIGEST_LENGTH), primary_key=True)
    binder_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    normalized_folder: Mapped[str] = mapped_column(Text, primary_key=True)
    state_schema_version: Mapped[str | None] = mapped_column(String(_SCHEMA_VERSION_LENGTH), nullable=True)
    state_digest: Mapped[str | None] = mapped_column(String(_DIGEST_LENGTH), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


__all__ = [
    "AcceptedConfigurationRecord",
    "ChildExecutionRecord",
    "ChildThreadRecord",
    "CompositionSnapshotRecord",
    "ConfigurationSnapshotRecord",
    "CurrentConfigurationRecord",
    "EnvironmentBindingRecord",
    "SessionRecord",
]
