"""Relational heads for accepted generations, Threads, child executions, and Environment state."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    generation_digest: Mapped[str] = mapped_column(
        String(_DIGEST),
        ForeignKey("accepted_configuration.generation_digest", ondelete="CASCADE"),
        primary_key=True,
    )
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    normalized_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)


class DeletedThreadRecord(Base):
    """Permanent deletion evidence, independent of the removed Thread row."""

    __tablename__ = "deleted_thread"

    thread_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)


class CompactionRecord(Base):
    """Generation outcomes and independent Host settlement, awaiting pruning."""

    __tablename__ = "compaction"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'ready', 'failed')", name="status"),
        CheckConstraint(
            "(settled_at IS NULL AND disposition IS NULL) OR "
            "(settled_at IS NOT NULL AND disposition IS NOT NULL "
            "AND disposition IN ('applied', 'skipped', 'cleared', 'expired'))",
            name="settlement",
        ),
        Index("uq_compaction_unsettled_thread", "thread_id", unique=True, sqlite_where=text("settled_at IS NULL")),
        Index("ix_compaction_retention", "settled_at", "created_at", "operation_id"),
    )

    operation_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ToolExecutionHeadRecord(Base):
    """One retained call and its independently settled completion input."""

    __tablename__ = "tool_execution"

    execution_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(_ID), nullable=False, index=True)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    composition_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    foreground_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completion_state: Mapped[str] = mapped_column(String(16), nullable=False, default="waiting", index=True)


class ConnectorConfigurationRecord(Base):
    """Host-private Connector instance configuration, including plaintext credentials."""

    __tablename__ = "connector_configuration"
    __table_args__ = (
        Index("uq_connector_configuration_group_name", "group_name", unique=True, sqlite_where=text("deleted = 0")),
    )

    connector_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deliver_to_bot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selected_events: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[str] = mapped_column(Text, nullable=False)
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    group_name: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    rules: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")


class ConnectorStateRecord(Base):
    __tablename__ = "connector_state"

    connector_id: Mapped[str] = mapped_column(
        String(_ID), ForeignKey("connector_configuration.connector_id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")


class ConnectorSetupOfferRecord(Base):
    __tablename__ = "connector_setup_offer"

    offer_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class BotInboxRecord(Base):
    __tablename__ = "bot_inbox"
    __table_args__ = (
        CheckConstraint(
            "(source = 'connector' AND connector_id IS NOT NULL AND source_thread_id IS NULL) OR "
            "(source = 'heartbeat' AND connector_id IS NULL AND source_thread_id IS NULL AND event_type = 'heartbeat') OR "
            "(source = 'thread' AND connector_id IS NULL AND source_thread_id IS NOT NULL)",
            name="source",
        ),
        UniqueConstraint("connector_id", "source_event_id", name="connector_source_event"),
        Index("uq_bot_inbox_heartbeat", "source", unique=True, sqlite_where=text("source = 'heartbeat'")),
        Index("ix_bot_inbox_pending", "created_at", "input_id"),
    )

    input_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    connector_id: Mapped[str | None] = mapped_column(
        String(_ID), ForeignKey("connector_configuration.connector_id", ondelete="CASCADE"), nullable=True
    )
    source_thread_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_thread_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class BotNoteRecord(Base):
    __tablename__ = "bot_note"
    __table_args__ = (CheckConstraint("length(CAST(content AS BLOB)) BETWEEN 1 AND 16384", name="content_size"),)

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    last_writer_thread_id: Mapped[str] = mapped_column(String(80), nullable=False)


class BotStateRecord(Base):
    __tablename__ = "bot_state"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class BotInputRecord(Base):
    __tablename__ = "bot_input"

    input_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class BotMessageRecord(Base):
    __tablename__ = "bot_message"

    message_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class PlannedRestartRecord(Base):
    """The only prepared/claimed update handoff for this local data root."""

    __tablename__ = "planned_restart"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class WebPushKeyRecord(Base):
    __tablename__ = "web_push_key"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    private_key: Mapped[str] = mapped_column(Text, nullable=False)


class WebPushSubscriptionRecord(Base):
    __tablename__ = "web_push_subscription"

    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[Literal["all", "bot"]] = mapped_column(String(8), nullable=False, server_default="all")
    language: Mapped[Literal["en", "zh-CN"]] = mapped_column(String(8), nullable=False, server_default="en")
    last_active_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class ShareGrantRecord(Base):
    """Revocable guest access; the secret itself is never retained."""

    __tablename__ = "share_grant"
    __table_args__ = (
        CheckConstraint("scope IN ('project', 'thread')", name="scope"),
        CheckConstraint("permission IN ('view', 'prompt')", name="permission"),
    )

    share_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(_ID), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class ProjectModelPreferenceRecord(Base):
    __tablename__ = "project_model_preference"

    project_id: Mapped[str] = mapped_column(String(_ID), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(_ID), nullable=False)


class CoordinatorRecord(Base):
    __tablename__ = "coordinator"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="RESTRICT"), primary_key=True
    )
    auto_followup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))


class CoordinatorWorkerRecord(Base):
    """A managed root conversation; unrelated to subagent execution lineage."""

    __tablename__ = "coordinator_worker"

    worker_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    coordinator_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("coordinator.thread_id", ondelete="RESTRICT"), nullable=False, index=True
    )


class ThreadRecord(Base):
    __tablename__ = "thread"

    memory_scope: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    thread_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_thread_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=True, index=True
    )
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    first_input: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default=text("''"))
    latest_input: Mapped[str] = mapped_column(String(2048), nullable=False, default="", server_default=text("''"))
    latest_reply: Mapped[str] = mapped_column(String(2048), nullable=False, default="", server_default=text("''"))
    reply_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="none", server_default="none")
    activity_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True, index=True)
    touched_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)
    initial_state_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_state_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    continuation_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    continuation_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    read_model_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    read_model_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read_model_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_execution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    read_state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completion_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completion_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completion_digest: Mapped[str | None] = mapped_column(String(_DIGEST), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


Index(
    "ix_thread_unread",
    ThreadRecord.memory_scope,
    ThreadRecord.unread,
    ThreadRecord.parent_thread_id,
    ThreadRecord.archived,
    ThreadRecord.updated_at,
    ThreadRecord.thread_id,
    sqlite_where=text("unread = 1 AND memory_scope IS NULL AND parent_thread_id IS NULL AND archived IS 0"),
)


class LiveTranscriptHeadRecord(Base):
    """Voice transcript authority, independent of ordinary execution checkpoints."""

    __tablename__ = "live_transcript_head"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    object_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)


class ThreadWorkRecord(Base):
    """Small, replaceable work projection independent of transcript indexes."""

    __tablename__ = "thread_work"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    object_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    tasks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThreadInspectionRecord(Base):
    """Rebuildable inspection index for one exact selected Thread source."""

    __tablename__ = "thread_inspection"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


class TranscriptEntryRecord(Base):
    __tablename__ = "transcript_entry"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread_inspection.thread_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


class TranscriptTurnRecord(Base):
    __tablename__ = "transcript_turn"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread_inspection.thread_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_position: Mapped[int] = mapped_column(Integer, nullable=False)
    end_position: Mapped[int] = mapped_column(Integer, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


class WebuiDraftRecord(Base):
    """Current bounded editing state, independent of Thread execution configuration."""

    __tablename__ = "webui_draft"
    __table_args__ = (
        CheckConstraint("length(document_update) + length(options_update) <= 524288", name="document_size"),
    )

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    draft_id: Mapped[str] = mapped_column(String(100), nullable=False)
    document_update: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    options_update: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    unsent_since: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, index=True)


class ThreadConfigurationRecord(Base):
    __tablename__ = "thread_configuration"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "agent_source_kind IN ('agent', 'markdown', 'memory', 'live', 'bot', 'dynamic')", name="agent_source_kind"
        ),
    )

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(_ID), nullable=True)
    agent_source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_source_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    default_model_id: Mapped[str | None] = mapped_column(String(_ID), nullable=True)
    environment_profile_id: Mapped[str] = mapped_column(String(_ID), nullable=False)
    local_roots_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    environment_bindings_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    default_environment: Mapped[str | None] = mapped_column(String(63), nullable=True)
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


class McpOAuthRecord(Base):
    """App-private registration and grant; references alone enter captured recipes."""

    __tablename__ = "mcp_oauth"
    __table_args__ = (
        UniqueConstraint("owner", name="owner"),
        CheckConstraint("credential_version >= 0", name="credential_version"),
    )

    binding_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    configuration_digest: Mapped[str] = mapped_column(String(_DIGEST), nullable=False)
    generation: Mapped[str] = mapped_column(String(80), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_pending: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bundle_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageModelRecord(Base):
    """The configured Model resource selected by an immutable Harness UI recipe."""

    __tablename__ = "usage_model"
    recipe_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_id: Mapped[str | None] = mapped_column(String(128))
    route: Mapped[str | None] = mapped_column(String(512))


class ThreadUsageRecord(Base):
    """First observed canonical usage contribution within one root Thread family."""

    __tablename__ = "thread_usage"
    __table_args__ = (UniqueConstraint("root_thread_id", "record_id", name="identity"),)

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    descendant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)


class EnvdSessionBindingRecord(Base):
    __tablename__ = "envd_session_binding"

    thread_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("thread.thread_id", ondelete="CASCADE"), primary_key=True
    )
    device_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    head_json: Mapped[str] = mapped_column(Text, nullable=False)


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
    device_id: Mapped[str] = mapped_column(String(_ID), primary_key=True, server_default=text("''"))
    alias: Mapped[str] = mapped_column(String(63), primary_key=True, server_default=text("''"))
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
    "ThreadUsageRecord",
]
