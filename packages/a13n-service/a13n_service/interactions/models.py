"""Relational authority for durable Agent interactions and worker generations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from a13n_harness import SafeFailure
from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.labels import LABELS_SQL_TYPE
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.temporal import assume_utc, optional_assume_utc

from .domain import (
    ExecutionBudget,
    JsonObject,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunAttemptYieldReason,
    RunInputKind,
    RunLineageKind,
    RunPayloadObjectRef,
    RunPendingSummary,
    RunStatus,
    RunUsage,
    RunUsageLimit,
    RunWaitReason,
    SealedRunState,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)

_MODEL_OBSERVATION_ADAPTER = TypeAdapter(ModelExecutionObservation)
_RUN_USAGE_ADAPTER = TypeAdapter(RunUsage)
_RUN_USAGE_LIMIT_ADAPTER = TypeAdapter(RunUsageLimit | None)
_PENDING_ADAPTER = TypeAdapter(RunPendingSummary | None)
_FAILURE_ADAPTER = TypeAdapter(SafeFailure | None)
_JSON_OBJECTS_ADAPTER = TypeAdapter(tuple[JsonObject, ...])
_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject | None)
_RUN_PAYLOAD_REF_ADAPTER = TypeAdapter(RunPayloadObjectRef)
_SEALED_STATE_ADAPTER = TypeAdapter(SealedRunState)


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id", name="uq_sessions_organization_id"),
        ForeignKeyConstraint(
            ("configuration_draft_id", "organization_id", "id"),
            ("configuration_drafts.id", "configuration_drafts.organization_id", "configuration_drafts.session_id"),
            name="fk_sessions_configuration_draft",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "(configuration_owner_user_id IS NULL AND configuration_draft_id IS NULL) OR "
            "(configuration_owner_user_id IS NOT NULL AND configuration_draft_id IS NOT NULL)",
            name="configuration_scope_valid",
        ),
        Index("ix_sessions_configuration_owner", "workspace_id", "configuration_owner_user_id", "updated_at", "id"),
        Index("ix_sessions_workspace_created", "organization_id", "workspace_id", "created_at", "id"),
        Index("ix_sessions_workspace_updated", "organization_id", "workspace_id", "updated_at", "id"),
        Index("ix_sessions_labels", "labels", postgresql_using="gin", postgresql_ops={"labels": "jsonb_path_ops"}),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    configuration_owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    configuration_draft_id: Mapped[str | None] = mapped_column(String(72))
    labels: Mapped[dict[str, str]] = mapped_column(
        LABELS_SQL_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Session:
        return Session(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            labels=self.labels or {},
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ThreadRecord(Base):
    __tablename__ = "threads"
    __table_args__ = (
        ForeignKeyConstraint(
            ("organization_id", "session_id"),
            ("sessions.organization_id", "sessions.id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("organization_id", "origin_thread_id"),
            ("threads.organization_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "origin_thread_id", "origin_run_id"),
            ("runs.organization_id", "runs.thread_id", "runs.id"),
            name="fk_threads_origin_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id", "id", "head_run_id"),
            ("runs.organization_id", "runs.session_id", "runs.thread_id", "runs.id"),
            name="fk_threads_head_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id", "id", "current_run_id"),
            ("runs.organization_id", "runs.session_id", "runs.thread_id", "runs.id"),
            name="fk_threads_current_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("queue_version >= 0", name="queue_version_non_negative"),
        CheckConstraint("next_delivery_sequence >= 1", name="next_delivery_sequence_positive"),
        CheckConstraint("pending_count >= 0", name="pending_count_non_negative"),
        CheckConstraint("pending_bytes >= 0", name="pending_bytes_non_negative"),
        CheckConstraint("role IN ('root', 'child')", name="role_valid"),
        CheckConstraint("origin_kind IN ('new', 'fork', 'child')", name="origin_kind_valid"),
        CheckConstraint(
            "(origin_kind = 'new' AND role = 'root' AND origin_thread_id IS NULL AND origin_run_id IS NULL) "
            "OR (origin_kind = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL) "
            "OR (origin_kind = 'child' AND role = 'child' AND origin_thread_id IS NOT NULL "
            "AND origin_run_id IS NOT NULL)",
            name="origin_shape_valid",
        ),
        UniqueConstraint("organization_id", "id", name="uq_threads_organization_id"),
        UniqueConstraint("organization_id", "session_id", "id", name="uq_threads_session_id"),
        Index(
            "uq_threads_session_root",
            "organization_id",
            "session_id",
            unique=True,
            postgresql_where=text("role = 'root'"),
        ),
        Index("ix_threads_session_created", "organization_id", "session_id", "created_at", "id"),
        Index("ix_threads_session_updated", "organization_id", "session_id", "updated_at", "id"),
        Index("ix_threads_selected_run", "organization_id", text("coalesce(current_run_id, head_run_id)")),
        Index("ix_threads_origin_run", "organization_id", "origin_run_id", "id"),
        Index("ix_threads_origin_thread", "organization_id", "origin_thread_id", "id"),
        Index("ix_threads_labels", "labels", postgresql_using="gin", postgresql_ops={"labels": "jsonb_path_ops"}),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    queue_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    next_delivery_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    pending_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    pending_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_thread_id: Mapped[str | None] = mapped_column(String(72))
    origin_run_id: Mapped[str | None] = mapped_column(String(72))
    head_run_id: Mapped[str | None] = mapped_column(String(72))
    current_run_id: Mapped[str | None] = mapped_column(String(72))
    default_environment_id: Mapped[str | None] = mapped_column(ForeignKey("environments.id"))
    labels: Mapped[dict[str, str]] = mapped_column(
        LABELS_SQL_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Thread:
        return Thread(
            id=self.id,
            version=self.version,
            queue_version=self.queue_version,
            organization_id=self.organization_id,
            session_id=self.session_id,
            role=ThreadRole(self.role),
            origin_kind=ThreadOriginKind(self.origin_kind),
            origin_thread_id=self.origin_thread_id,
            origin_run_id=self.origin_run_id,
            head_run_id=self.head_run_id,
            current_run_id=self.current_run_id,
            default_environment_id=self.default_environment_id,
            labels=self.labels or {},
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class RunRecord(Base):
    __tablename__ = "runs"
    configuration_context: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    configuration_draft_id: Mapped[str | None] = mapped_column(String(72))
    __table_args__ = (
        ForeignKeyConstraint(
            ("configuration_draft_id", "organization_id", "session_id"),
            (
                "configuration_drafts.id",
                "configuration_drafts.organization_id",
                "configuration_drafts.session_id",
            ),
            name="fk_runs_configuration_draft_same_session",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "(configuration_draft_id IS NULL AND configuration_context IS NULL AND agent_revision_id IS NOT NULL) OR "
            "(configuration_draft_id IS NOT NULL AND configuration_context IS NOT NULL AND agent_revision_id IS NULL)",
            name="configuration_binding_consistent",
        ),
        CheckConstraint(
            "(environment_id IS NULL AND environment_access IS NULL AND environment_use_started_at IS NULL) OR (environment_id IS NOT NULL AND environment_access IN ('read_only','read_write','full'))",
            name="environment_selection_valid",
        ),
        Index("ix_runs_labels", "labels", postgresql_using="gin", postgresql_ops={"labels": "jsonb_path_ops"}),
        ForeignKeyConstraint(
            ("organization_id", "session_id", "thread_id"),
            ("threads.organization_id", "threads.session_id", "threads.id"),
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ("organization_id", "parent_run_id"),
            ("runs.organization_id", "runs.id"),
            name="fk_runs_parent_same_organization",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "thread_id", "retry_of_run_id"),
            ("runs.organization_id", "runs.thread_id", "runs.id"),
            name="fk_runs_retry_same_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "id", "current_run_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            name="fk_runs_current_attempt_same_run",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("organization_id", "id", "sealed_state_committed_by_run_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            name="fk_runs_sealed_attempt_same_run",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("authority_principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint("lineage_kind IN ('root', 'continue', 'fork')", name="lineage_kind_valid"),
        CheckConstraint(
            "(lineage_kind = 'root' AND parent_run_id IS NULL) "
            "OR (lineage_kind IN ('continue', 'fork') AND parent_run_id IS NOT NULL)",
            name="lineage_shape_valid",
        ),
        CheckConstraint(
            "status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "input_kind IN ('agent_input', 'waiting_feedback', 'waiting_continue', 'async_subagent_result')",
            name="input_kind_valid",
        ),
        CheckConstraint(
            "wait_reason IS NULL OR wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')",
            name="wait_reason_valid",
        ),
        CheckConstraint(
            "max_attempts >= 0 AND max_handoffs >= 0 AND attempts_started >= 0 "
            "AND attempts_charged >= 0 AND handoffs_completed >= 0",
            name="execution_values_non_negative",
        ),
        CheckConstraint(
            "attempts_charged <= max_attempts "
            "AND handoffs_completed <= max_handoffs "
            "AND attempts_charged <= attempts_started "
            "AND attempts_started <= attempts_charged + handoffs_completed",
            name="execution_counts_valid",
        ),
        CheckConstraint(
            "(input_object_key IS NULL AND input_object_digest_sha256 IS NULL AND input_object_size_bytes IS NULL "
            "AND input_object_content_type IS NULL AND input_object_schema_version IS NULL) OR "
            "(input_object_key IS NOT NULL AND input_object_digest_sha256 IS NOT NULL AND input_object_size_bytes > 0 "
            "AND input_object_content_type IS NOT NULL AND input_object_schema_version IS NOT NULL)",
            name="input_object_group_valid",
        ),
        CheckConstraint(
            "(input_json IS NOT NULL) <> (input_object_key IS NOT NULL)", name="input_representation_valid"
        ),
        CheckConstraint(
            "(output_object_key IS NULL AND output_object_digest_sha256 IS NULL AND output_object_size_bytes IS NULL "
            "AND output_object_content_type IS NULL AND output_object_schema_version IS NULL) OR "
            "(output_object_key IS NOT NULL AND output_object_digest_sha256 IS NOT NULL "
            "AND output_object_size_bytes > 0 AND output_object_content_type IS NOT NULL "
            "AND output_object_schema_version IS NOT NULL)",
            name="output_object_group_valid",
        ),
        CheckConstraint(
            "(status = 'completed' AND (output_json IS NOT NULL) <> (output_object_key IS NOT NULL) "
            "AND completed_at IS NOT NULL) OR (status <> 'completed' AND output_json IS NULL "
            "AND output_object_key IS NULL AND output_text IS NULL AND completed_at IS NULL)",
            name="output_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'waiting' AND wait_reason IS NOT NULL AND pending_json IS NOT NULL AND waiting_at IS NOT NULL) "
            "OR (status <> 'waiting' AND wait_reason IS NULL AND pending_json IS NULL AND waiting_at IS NULL)",
            name="waiting_lifecycle_valid",
        ),
        CheckConstraint(
            "(status IN ('failed', 'cancelled') AND failure_json IS NOT NULL) "
            "OR (status NOT IN ('failed', 'cancelled') AND failure_json IS NULL)",
            name="failure_lifecycle_valid",
        ),
        CheckConstraint(
            "(sealed_state_digest_sha256 IS NULL AND sealed_state_size_bytes IS NULL "
            "AND sealed_state_content_type IS NULL AND sealed_state_envelope_schema_version IS NULL "
            "AND sealed_state_harness_schema_version IS NULL AND sealed_state_checkpoint_seq IS NULL "
            "AND sealed_state_committed_by_run_attempt_id IS NULL) OR "
            "(sealed_state_digest_sha256 IS NOT NULL AND sealed_state_size_bytes > 0 "
            "AND sealed_state_content_type IS NOT NULL AND sealed_state_envelope_schema_version IS NOT NULL "
            "AND sealed_state_harness_schema_version IS NOT NULL AND sealed_state_checkpoint_seq >= 0)",
            name="sealed_state_group_valid",
        ),
        CheckConstraint(
            "(status IN ('waiting', 'completed') AND sealed_state_digest_sha256 IS NOT NULL "
            "AND sealed_state_checkpoint_seq > 0 AND sealed_state_committed_by_run_attempt_id IS NOT NULL) "
            "OR (status IN ('accepted', 'running', 'cancelled') AND sealed_state_digest_sha256 IS NULL) "
            "OR status = 'failed'",
            name="sealed_state_lifecycle_valid",
        ),
        CheckConstraint(
            "(status IN ('waiting', 'completed', 'failed', 'cancelled')) = (sealed_at IS NOT NULL)",
            name="sealed_at_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'accepted' AND current_run_attempt_id IS NULL AND attempts_started = 0) "
            "OR (status = 'running' AND attempts_started >= 1) "
            "OR (status IN ('waiting', 'completed', 'failed', 'cancelled') "
            "AND current_run_attempt_id IS NULL)",
            name="current_attempt_lifecycle_valid",
        ),
        CheckConstraint("length(effective_agent_config_digest) = 64", name="effective_config_digest_sha256"),
        CheckConstraint("length(request_fingerprint) = 64", name="request_fingerprint_sha256"),
        CheckConstraint(
            "(input_object_digest_sha256 IS NULL OR length(input_object_digest_sha256) = 64) AND "
            "(output_object_digest_sha256 IS NULL OR length(output_object_digest_sha256) = 64) AND "
            "(sealed_state_digest_sha256 IS NULL OR length(sealed_state_digest_sha256) = 64)",
            name="optional_digests_sha256",
        ),
        UniqueConstraint("organization_id", "id", name="uq_runs_organization_id"),
        UniqueConstraint("organization_id", "thread_id", "id", name="uq_runs_organization_thread_id"),
        Index(
            "uq_runs_thread_authority",
            "organization_id",
            "thread_id",
            "id",
            "authority_principal_type",
            "authority_principal_id",
            unique=True,
        ),
        UniqueConstraint(
            "organization_id",
            "session_id",
            "thread_id",
            "id",
            name="uq_runs_scope_identity",
        ),
        Index(
            "uq_runs_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_runs_active_thread",
            "organization_id",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('accepted', 'running')"),
        ),
        Index(
            "uq_runs_live_root_thread",
            "organization_id",
            "thread_id",
            unique=True,
            postgresql_where=text(
                "parent_run_id IS NULL AND status IN ('accepted', 'running', 'waiting', 'completed')"
            ),
        ),
        Index(
            "ix_runs_worker_scan",
            "organization_id",
            "queue_name",
            "status",
            "available_at",
            "priority",
            "created_at",
            "id",
            postgresql_where=text("status = 'accepted' OR (status = 'running' AND current_run_attempt_id IS NULL)"),
        ),
        Index("ix_runs_session_created", "organization_id", "session_id", "created_at", "id"),
        Index("ix_runs_agent_session", "organization_id", "agent_id", "session_id", "id"),
        Index("ix_runs_status_session", "organization_id", "status", "session_id", "id"),
        Index("ix_runs_trigger_session", "organization_id", "trigger_type", "session_id", "id"),
        Index("ix_runs_thread_created", "organization_id", "thread_id", "created_at", "id"),
        Index("ix_runs_parent", "organization_id", "parent_run_id", "id"),
        Index("ix_runs_retry", "organization_id", "retry_of_run_id", "id"),
    )

    environment_id: Mapped[str | None] = mapped_column(ForeignKey("environments.id"), index=True)
    environment_access: Mapped[str | None] = mapped_column(String(16))
    environment_use_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    authority_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(
        LABELS_SQL_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    parent_run_id: Mapped[str | None] = mapped_column(String(72))
    retry_of_run_id: Mapped[str | None] = mapped_column(String(72))
    lineage_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(256), nullable=False)
    trigger_entity_type: Mapped[str | None] = mapped_column(String(256))
    trigger_entity_id: Mapped[str | None] = mapped_column(String(256))
    parent_agent_instance_id: Mapped[str | None] = mapped_column(String(256))
    delegation_id: Mapped[str | None] = mapped_column(String(256))
    parent_tool_call_id: Mapped[str | None] = mapped_column(String(256))
    agent_id: Mapped[str] = mapped_column(String(72), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False)
    agent_revision_id: Mapped[str | None] = mapped_column(
        String(72), ForeignKey("agent_revisions.id", ondelete="RESTRICT")
    )
    effective_agent_config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_execution_observation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    connection_selections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    native_tool_contexts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    bot_memory_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_name: Mapped[str] = mapped_column(String(256), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    execution_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_handoffs: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    attempts_started: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts_charged: Mapped[int] = mapped_column(Integer, nullable=False)
    handoffs_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_charged_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(256))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    wait_reason: Mapped[str | None] = mapped_column(String(16))
    pending_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    input_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    input_object_key: Mapped[str | None] = mapped_column(String(1024))
    input_object_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    input_object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    input_object_content_type: Mapped[str | None] = mapped_column(String(255))
    input_object_schema_version: Mapped[str | None] = mapped_column(String(32))
    input_text: Mapped[str | None] = mapped_column(String(65536))
    output_json: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    output_object_key: Mapped[str | None] = mapped_column(String(1024))
    output_object_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    output_object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    output_object_content_type: Mapped[str | None] = mapped_column(String(255))
    output_object_schema_version: Mapped[str | None] = mapped_column(String(32))
    output_text: Mapped[str | None] = mapped_column(String(65536))
    failure_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    sealed_state_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    sealed_state_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sealed_state_content_type: Mapped[str | None] = mapped_column(String(255))
    sealed_state_envelope_schema_version: Mapped[str | None] = mapped_column(String(32))
    sealed_state_harness_schema_version: Mapped[str | None] = mapped_column(String(32))
    sealed_state_checkpoint_seq: Mapped[int | None] = mapped_column(BigInteger)
    sealed_state_committed_by_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> Run:
        values: dict[str, Any] = {
            "configuration_context": self.configuration_context,
            "id": self.id,
            "version": self.version,
            "organization_id": self.organization_id,
            "authority_principal": PrincipalRef(
                principal_type=PrincipalType(self.authority_principal_type),
                principal_id=self.authority_principal_id,
            ),
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "labels": self.labels,
            "parent_run_id": self.parent_run_id,
            "retry_of_run_id": self.retry_of_run_id,
            "environment_id": self.environment_id,
            "environment_access": self.environment_access,
            "environment_use_started_at": optional_assume_utc(self.environment_use_started_at),
            "lineage_kind": RunLineageKind(self.lineage_kind),
            "trigger_type": self.trigger_type,
            "trigger_entity_type": self.trigger_entity_type,
            "trigger_entity_id": self.trigger_entity_id,
            "parent_agent_instance_id": self.parent_agent_instance_id,
            "delegation_id": self.delegation_id,
            "parent_tool_call_id": self.parent_tool_call_id,
            "agent_id": self.agent_id,
            "agent_revision_id": self.agent_revision_id,
            "effective_agent_config_digest": self.effective_agent_config_digest,
            "model_execution_observation": _MODEL_OBSERVATION_ADAPTER.validate_python(
                self.model_execution_observation_json
            ),
            "connection_selections": _JSON_OBJECTS_ADAPTER.validate_python(self.connection_selections_json),
            "native_tool_contexts": _JSON_OBJECTS_ADAPTER.validate_python(self.native_tool_contexts_json),
            "bot_memory": self.bot_memory_json,
            "priority": self.priority,
            "queue_name": self.queue_name,
            "available_at": assume_utc(self.available_at),
            "current_run_attempt_id": self.current_run_attempt_id,
            "execution_budget": ExecutionBudget(
                policy_version=self.execution_policy_version,
                max_attempts=self.max_attempts,
                max_handoffs=self.max_handoffs,
                execution_deadline_at=optional_assume_utc(self.execution_deadline_at),
                max_usage=_RUN_USAGE_LIMIT_ADAPTER.validate_python(self.max_usage_json),
            ),
            "attempts_started": self.attempts_started,
            "attempts_charged": self.attempts_charged,
            "handoffs_completed": self.handoffs_completed,
            "usage_charged": _RUN_USAGE_ADAPTER.validate_python(self.usage_charged_json),
            "idempotency_key": self.idempotency_key,
            "request_fingerprint": self.request_fingerprint,
            "status": RunStatus(self.status),
            "wait_reason": None if self.wait_reason is None else RunWaitReason(self.wait_reason),
            "input_kind": RunInputKind(self.input_kind),
            "input_text": self.input_text,
            "output_text": self.output_text,
            "failure": _FAILURE_ADAPTER.validate_python(self.failure_json),
            "pending": _PENDING_ADAPTER.validate_python(self.pending_json),
            "sealed_state": self._sealed_state(),
            "created_at": assume_utc(self.created_at),
            "updated_at": assume_utc(self.updated_at),
            "started_at": optional_assume_utc(self.started_at),
            "waiting_at": optional_assume_utc(self.waiting_at),
            "completed_at": optional_assume_utc(self.completed_at),
            "sealed_at": optional_assume_utc(self.sealed_at),
        }
        if self.input_object_key is None:
            values["input"] = _json_null(self.input_json)
        else:
            values["input_object"] = self._input_object_ref()
        if self.status == RunStatus.completed:
            if self.output_object_key is None:
                values["output"] = _json_null(self.output_json)
            else:
                values["output_object"] = self._output_object_ref()
        return Run.model_validate(values)

    def _input_object_ref(self) -> RunPayloadObjectRef:
        return _RUN_PAYLOAD_REF_ADAPTER.validate_python(
            {
                "object_key": self.input_object_key,
                "digest_sha256": self.input_object_digest_sha256,
                "size_bytes": self.input_object_size_bytes,
                "content_type": self.input_object_content_type,
                "schema_version": self.input_object_schema_version,
            }
        )

    def _output_object_ref(self) -> RunPayloadObjectRef:
        return _RUN_PAYLOAD_REF_ADAPTER.validate_python(
            {
                "object_key": self.output_object_key,
                "digest_sha256": self.output_object_digest_sha256,
                "size_bytes": self.output_object_size_bytes,
                "content_type": self.output_object_content_type,
                "schema_version": self.output_object_schema_version,
            }
        )

    def _sealed_state(self) -> SealedRunState | None:
        if self.sealed_state_digest_sha256 is None:
            return None
        return _SEALED_STATE_ADAPTER.validate_python(
            {
                "digest_sha256": self.sealed_state_digest_sha256,
                "size_bytes": self.sealed_state_size_bytes,
                "content_type": self.sealed_state_content_type,
                "envelope_schema_version": self.sealed_state_envelope_schema_version,
                "harness_schema_version": self.sealed_state_harness_schema_version,
                "checkpoint_seq": self.sealed_state_checkpoint_seq,
                "committed_by_run_attempt_id": self.sealed_state_committed_by_run_attempt_id,
            }
        )


class RunAttemptRecord(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ("organization_id", "run_id"),
            ("runs.organization_id", "runs.id"),
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id", "replaces_run_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "yield_reason IS NULL OR yield_reason = 'service_drain'",
            name="yield_reason_valid",
        ),
        CheckConstraint("length(lease_token_digest) = 64", name="lease_token_digest_sha256"),
        CheckConstraint("length(worker_id) BETWEEN 1 AND 256", name="worker_id_bounded"),
        CheckConstraint("length(worker_build_id) BETWEEN 1 AND 256", name="worker_build_id_bounded"),
        CheckConstraint(
            "(status IN ('succeeded', 'yielded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="finished_at_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
            "OR (status = 'running' AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
            "OR (status IN ('succeeded', 'yielded', 'failed', 'cancelled') "
            "AND ((harness_run_id IS NULL AND started_at IS NULL) "
            "OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))",
            name="harness_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'yielded' AND yield_reason IS NOT NULL AND failure_json IS NULL) "
            "OR (status <> 'yielded' AND yield_reason IS NULL)",
            name="yield_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_json IS NOT NULL) "
            "OR (status IN ('leased', 'running', 'succeeded', 'yielded') AND failure_json IS NULL) "
            "OR status = 'cancelled'",
            name="failure_lifecycle_valid",
        ),
        Index("uq_run_attempts_organization_id", "organization_id", "id", unique=True),
        UniqueConstraint("organization_id", "run_id", "id", name="uq_run_attempts_run_id"),
        Index(
            "uq_run_attempts_fence_identity",
            "organization_id",
            "run_id",
            "id",
            "attempt_number",
            unique=True,
        ),
        Index("uq_run_attempts_number", "organization_id", "run_id", "attempt_number", unique=True),
        Index(
            "ix_run_attempts_live_lease",
            "organization_id",
            "status",
            "lease_expires_at",
            "run_id",
            postgresql_where=text("status IN ('leased', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    attempt_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    replaces_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    start_reason: Mapped[str | None] = mapped_column(String(256))
    worker_id: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_build_id: Mapped[str] = mapped_column(String(256), nullable=False)
    harness_run_id: Mapped[str | None] = mapped_column(String(256))
    model_execution_observation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    yield_reason: Mapped[str | None] = mapped_column(String(32))
    failure_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> RunAttempt:
        return RunAttempt(
            id=self.id,
            version=self.version,
            organization_id=self.organization_id,
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            status=RunAttemptStatus(self.status),
            replaces_run_attempt_id=self.replaces_run_attempt_id,
            start_reason=self.start_reason,
            worker_id=self.worker_id,
            worker_build_id=self.worker_build_id,
            harness_run_id=self.harness_run_id,
            model_execution_observation=_MODEL_OBSERVATION_ADAPTER.validate_python(
                self.model_execution_observation_json
            ),
            lease_token_digest=self.lease_token_digest,
            lease_expires_at=assume_utc(self.lease_expires_at),
            heartbeat_at=assume_utc(self.heartbeat_at),
            usage=_RUN_USAGE_ADAPTER.validate_python(self.usage_json),
            yield_reason=None if self.yield_reason is None else RunAttemptYieldReason(self.yield_reason),
            failure=_FAILURE_ADAPTER.validate_python(self.failure_json),
            created_at=assume_utc(self.created_at),
            started_at=optional_assume_utc(self.started_at),
            finished_at=optional_assume_utc(self.finished_at),
            updated_at=assume_utc(self.updated_at),
        )


def _json_null(value: Any) -> Any:
    return None if value is JSON.NULL else value


class RunUsageRecord(Base):
    """Immutable Harness receipt; Run and Attempt retain its accepted attribution."""

    __tablename__ = "run_usage_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ("organization_id", "run_id", "run_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(content_digest) = 64", name="content_digest_sha256"),
        Index("ix_run_usage_records_attempt", "organization_id", "run_attempt_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_attempt_id: Mapped[str] = mapped_column(String(72), nullable=False)
    harness_run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["RunAttemptRecord", "RunRecord", "RunUsageRecord", "SessionRecord", "ThreadRecord"]
