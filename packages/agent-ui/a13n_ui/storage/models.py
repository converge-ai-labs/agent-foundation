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


class SessionRecord(Base):
    """SQLite-owned Session identity, composition selection, and display control."""

    __tablename__ = "local_session"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('provisioning', 'ready', 'blocked', 'deleting', 'cleanup_pending', 'deleted')",
            name="lifecycle_state",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    creation_request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    root_thread_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("session_thread.thread_id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    lifecycle_failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    control_revision: Mapped[int] = mapped_column(Integer, nullable=False)
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


class SessionThreadRecord(Base):
    """One independently advancing Harness continuation lineage."""

    __tablename__ = "session_thread"
    __table_args__ = (
        UniqueConstraint("thread_id", "session_id", name="thread_session_identity"),
        UniqueConstraint("session_id", "root_ordinal", name="session_root"),
    )

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    root_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queue_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_checkpoint_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("thread_checkpoint.checkpoint_id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    active_turn_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("thread_turn.turn_id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TurnRecord(Base):
    """Host-accepted advancement of exactly one Thread."""

    __tablename__ = "thread_turn"
    __table_args__ = (
        ForeignKeyConstraint(
            ("thread_id", "session_id"),
            ("session_thread.thread_id", "session_thread.session_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("turn_id", "thread_id", name="turn_thread_identity"),
        CheckConstraint(
            "state IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'interrupted')",
            name="state",
        ),
        CheckConstraint(
            "waiting_reason IS NULL OR waiting_reason IN ('deferred_tool', 'approval', 'external_input')",
            name="waiting_reason",
        ),
    )

    turn_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    base_checkpoint_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("thread_checkpoint.checkpoint_id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    selected_checkpoint_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("thread_checkpoint.checkpoint_id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    terminal_projection_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadCheckpointRecord(Base):
    """Durable selection metadata for one complete HarnessState object."""

    __tablename__ = "thread_checkpoint"
    __table_args__ = (
        ForeignKeyConstraint(
            ("owner_turn_id", "thread_id"),
            ("thread_turn.turn_id", "thread_turn.thread_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "owner_kind IN ('session_baseline', 'session_fork', 'root_turn')",
            name="owner_kind",
        ),
        CheckConstraint(
            "(owner_kind = 'root_turn' AND owner_turn_id IS NOT NULL) OR "
            "(owner_kind != 'root_turn' AND owner_turn_id IS NULL)",
            name="owner_shape",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_turn_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    state_object_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    harness_release: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TurnRunRecord(Base):
    """Ordered process-local Harness Runs belonging to one Host Turn."""

    __tablename__ = "turn_run"
    __table_args__ = (UniqueConstraint("run_id", name="run_identity"),)

    turn_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("thread_turn.turn_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingDeferredRecord(Base):
    """One exact unconsumed deferred request selected by a waiting Turn."""

    __tablename__ = "pending_deferred"

    turn_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("thread_turn.turn_id", ondelete="CASCADE"),
        primary_key=True,
    )
    object_digest: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_codec_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    consumed_by_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)


class PendingSubmissionRecord(Base):
    """Queued user input not yet accepted as a Turn."""

    __tablename__ = "pending_submission"
    __table_args__ = (UniqueConstraint("thread_id", "ordinal", name="thread_order"),)

    submission_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("session_thread.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostEnvironmentResourceRecord(Base):
    """Provider lifecycle and fencing authority for one Host resource."""

    __tablename__ = "host_environment_resource"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('unprovisioned', 'creating', 'available', 'pausing', 'paused', 'resuming', "
            "'destroying', 'destroyed', 'missing', 'unknown', 'failed')",
            name="lifecycle_state",
        ),
        CheckConstraint(
            "resource_allocation IN ('single_from_spec', 'multiple_from_spec')",
            name="resource_allocation",
        ),
    )

    host_resource_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_spec_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    binding_parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    resource_allocation: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    operation_fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_provider_state_digest: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("immutable_object.logical_digest", ondelete="RESTRICT"),
        nullable=True,
    )
    last_operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_operation_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_operation_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionEnvironmentAssignmentRecord(Base):
    """Session root binding assignment to one Host provider resource."""

    __tablename__ = "session_environment_assignment"
    __table_args__ = (UniqueConstraint("session_id", "binding_name", "scope_key", name="scope_binding"),)

    assignment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    binding_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(63), nullable=False)
    permission_ceiling_json: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False, default="root")
    host_resource_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("host_environment_resource.host_resource_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventSegmentRecord(Base):
    """Rebuildable index for one immutable retained AG-UI segment."""

    __tablename__ = "event_segment"
    __table_args__ = (
        UniqueConstraint("session_id", "first_sequence", name="range_start"),
        UniqueConstraint("session_id", "last_sequence", name="range_end"),
        UniqueConstraint("logical_digest", name="logical_identity"),
    )

    segment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    first_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_segment_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logical_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionPresentationRecord(Base):
    """Mutable replay and projection watermarks for one Session."""

    __tablename__ = "session_presentation"

    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retention_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    projection_watermark: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_segment_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ItemProjectionRecord(Base):
    """Rebuildable semantic Item summary derived from retained AG-UI events."""

    __tablename__ = "item_projection"
    __table_args__ = (UniqueConstraint("session_id", "item_id", name="session_item"),)

    projection_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("local_session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    first_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    searchable_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "CompositionSnapshotRecord",
    "ConfigurationDiagnosticRecord",
    "ConfigurationGenerationRecord",
    "CurrentConfigurationRecord",
    "EventSegmentRecord",
    "GenerationResourceRecord",
    "HostEnvironmentResourceRecord",
    "ImmutableObjectRecord",
    "ItemProjectionRecord",
    "PendingDeferredRecord",
    "PendingSubmissionRecord",
    "RecoveryDiagnosticRecord",
    "ResourceRevisionRecord",
    "SessionEnvironmentAssignmentRecord",
    "SessionPresentationRecord",
    "SessionRecord",
    "SessionThreadRecord",
    "SkillPackageReferenceRecord",
    "StoreLeaseRecord",
    "ThreadCheckpointRecord",
    "TurnRecord",
    "TurnRunRecord",
]
