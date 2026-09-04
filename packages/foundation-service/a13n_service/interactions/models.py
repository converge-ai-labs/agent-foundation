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
from a13n_service.models.domain import ModelExecutionObservation
from a13n_service.temporal import assume_utc, optional_assume_utc

from .domain import (
    EncryptedRunConfigPayloadRef,
    JsonObject,
    RecoveryBudget,
    RecoveryUsage,
    RecoveryUsageLimit,
    Run,
    RunAttempt,
    RunAttemptStatus,
    RunAttemptYieldReason,
    RunInputKind,
    RunLineageKind,
    RunPayloadObjectRef,
    RunPendingSummary,
    RunStatus,
    RunWaitReason,
    SealedRunState,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)

_MODEL_OBSERVATION_ADAPTER = TypeAdapter(ModelExecutionObservation)
_RECOVERY_USAGE_ADAPTER = TypeAdapter(RecoveryUsage)
_RECOVERY_LIMIT_ADAPTER = TypeAdapter(RecoveryUsageLimit | None)
_PENDING_ADAPTER = TypeAdapter(RunPendingSummary | None)
_FAILURE_ADAPTER = TypeAdapter(SafeFailure | None)
_JSON_OBJECTS_ADAPTER = TypeAdapter(tuple[JsonObject, ...])
_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject | None)
_ENCRYPTED_CONFIG_REF_ADAPTER = TypeAdapter(EncryptedRunConfigPayloadRef)
_RUN_PAYLOAD_REF_ADAPTER = TypeAdapter(RunPayloadObjectRef)
_SEALED_STATE_ADAPTER = TypeAdapter(SealedRunState)


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "tenant_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_sessions_tenant_id"),
        Index("ix_sessions_workspace_created", "tenant_id", "workspace_id", "created_at", "id"),
        Index("ix_sessions_workspace_updated", "tenant_id", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Session:
        return Session(
            id=self.id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ThreadRecord(Base):
    __tablename__ = "threads"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "session_id"),
            ("sessions.tenant_id", "sessions.id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "origin_thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "origin_thread_id", "origin_run_id"),
            ("runs.tenant_id", "runs.thread_id", "runs.id"),
            name="fk_threads_origin_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("tenant_id", "session_id", "id", "head_run_id"),
            ("runs.tenant_id", "runs.session_id", "runs.thread_id", "runs.id"),
            name="fk_threads_head_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("tenant_id", "session_id", "id", "current_run_id"),
            ("runs.tenant_id", "runs.session_id", "runs.thread_id", "runs.id"),
            name="fk_threads_current_run_same_thread",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("queue_version >= 0", name="queue_version_non_negative"),
        CheckConstraint("role IN ('root', 'child')", name="role_valid"),
        CheckConstraint("origin_kind IN ('new', 'fork', 'child')", name="origin_kind_valid"),
        CheckConstraint(
            "(origin_kind = 'new' AND role = 'root' AND origin_thread_id IS NULL AND origin_run_id IS NULL) "
            "OR (origin_kind = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL) "
            "OR (origin_kind = 'child' AND role = 'child' AND origin_thread_id IS NOT NULL "
            "AND origin_run_id IS NOT NULL)",
            name="origin_shape_valid",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_threads_tenant_id"),
        UniqueConstraint("tenant_id", "session_id", "id", name="uq_threads_session_id"),
        Index(
            "uq_threads_session_root",
            "tenant_id",
            "session_id",
            unique=True,
            postgresql_where=text("role = 'root'"),
            sqlite_where=text("role = 'root'"),
        ),
        Index("ix_threads_session_created", "tenant_id", "session_id", "created_at", "id"),
        Index("ix_threads_session_updated", "tenant_id", "session_id", "updated_at", "id"),
        Index("ix_threads_origin_run", "tenant_id", "origin_run_id", "id"),
        Index("ix_threads_origin_thread", "tenant_id", "origin_thread_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    queue_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_thread_id: Mapped[str | None] = mapped_column(String(72))
    origin_run_id: Mapped[str | None] = mapped_column(String(72))
    head_run_id: Mapped[str | None] = mapped_column(String(72))
    current_run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Thread:
        return Thread(
            id=self.id,
            version=self.version,
            queue_version=self.queue_version,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            role=ThreadRole(self.role),
            origin_kind=ThreadOriginKind(self.origin_kind),
            origin_thread_id=self.origin_thread_id,
            origin_run_id=self.origin_run_id,
            head_run_id=self.head_run_id,
            current_run_id=self.current_run_id,
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "session_id", "thread_id"),
            ("threads.tenant_id", "threads.session_id", "threads.id"),
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "parent_run_id"),
            ("runs.tenant_id", "runs.id"),
            name="fk_runs_parent_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "thread_id", "retry_of_run_id"),
            ("runs.tenant_id", "runs.thread_id", "runs.id"),
            name="fk_runs_retry_same_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "id", "current_run_attempt_id"),
            ("run_attempts.tenant_id", "run_attempts.run_id", "run_attempts.id"),
            name="fk_runs_current_attempt_same_run",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ("tenant_id", "id", "sealed_state_committed_by_run_attempt_id"),
            ("run_attempts.tenant_id", "run_attempts.run_id", "run_attempts.id"),
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
        CheckConstraint("next_attempt_fence >= 1", name="next_attempt_fence_positive"),
        CheckConstraint(
            "max_recovery_attempts >= 0 AND max_handoffs >= 0 AND attempts_started >= 0 "
            "AND recovery_attempts_started >= 0 AND handoffs_completed >= 0",
            name="recovery_values_non_negative",
        ),
        CheckConstraint(
            "recovery_attempts_started <= max_recovery_attempts "
            "AND handoffs_completed <= max_handoffs "
            "AND recovery_attempts_started <= attempts_started "
            "AND attempts_started <= recovery_attempts_started + handoffs_completed",
            name="recovery_counts_valid",
        ),
        CheckConstraint(
            "(encrypted_config_object_key IS NULL AND encrypted_config_ciphertext_digest_sha256 IS NULL "
            "AND encrypted_config_protected_value_digest_sha256 IS NULL AND encrypted_config_size_bytes IS NULL "
            "AND encrypted_config_encryption_key_id IS NULL AND encrypted_config_schema_version IS NULL) OR "
            "(encrypted_config_object_key IS NOT NULL AND encrypted_config_ciphertext_digest_sha256 IS NOT NULL "
            "AND encrypted_config_protected_value_digest_sha256 IS NOT NULL AND encrypted_config_size_bytes > 0 "
            "AND encrypted_config_encryption_key_id IS NOT NULL AND encrypted_config_schema_version IS NOT NULL)",
            name="encrypted_config_group_valid",
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
        CheckConstraint("length(runtime_lock_digest) = 64", name="runtime_lock_digest_sha256"),
        CheckConstraint("length(request_fingerprint) = 64", name="request_fingerprint_sha256"),
        CheckConstraint(
            "(encrypted_config_ciphertext_digest_sha256 IS NULL OR "
            "length(encrypted_config_ciphertext_digest_sha256) = 64) AND "
            "(encrypted_config_protected_value_digest_sha256 IS NULL OR "
            "length(encrypted_config_protected_value_digest_sha256) = 64) AND "
            "(input_object_digest_sha256 IS NULL OR length(input_object_digest_sha256) = 64) AND "
            "(output_object_digest_sha256 IS NULL OR length(output_object_digest_sha256) = 64) AND "
            "(sealed_state_digest_sha256 IS NULL OR length(sealed_state_digest_sha256) = 64)",
            name="optional_digests_sha256",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_runs_tenant_id"),
        UniqueConstraint("tenant_id", "thread_id", "id", name="uq_runs_tenant_thread_id"),
        Index(
            "uq_runs_thread_authority",
            "tenant_id",
            "thread_id",
            "id",
            "authority_principal_type",
            "authority_principal_id",
            unique=True,
        ),
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "thread_id",
            "id",
            name="uq_runs_scope_identity",
        ),
        Index(
            "uq_runs_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_runs_active_thread",
            "tenant_id",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('accepted', 'running')"),
            sqlite_where=text("status IN ('accepted', 'running')"),
        ),
        Index(
            "uq_runs_live_root_thread",
            "tenant_id",
            "thread_id",
            unique=True,
            postgresql_where=text(
                "parent_run_id IS NULL AND status IN ('accepted', 'running', 'waiting', 'completed')"
            ),
            sqlite_where=text("parent_run_id IS NULL AND status IN ('accepted', 'running', 'waiting', 'completed')"),
        ),
        Index(
            "ix_runs_worker_scan",
            "tenant_id",
            "queue_name",
            "status",
            "available_at",
            "priority",
            "created_at",
            "id",
            postgresql_where=text("status = 'accepted' OR (status = 'running' AND current_run_attempt_id IS NULL)"),
            sqlite_where=text("status = 'accepted' OR (status = 'running' AND current_run_attempt_id IS NULL)"),
        ),
        Index("ix_runs_session_created", "tenant_id", "session_id", "created_at", "id"),
        Index("ix_runs_thread_created", "tenant_id", "thread_id", "created_at", "id"),
        Index("ix_runs_parent", "tenant_id", "parent_run_id", "id"),
        Index("ix_runs_retry", "tenant_id", "retry_of_run_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    authority_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
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
    agent_revision_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("agent_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    effective_agent_config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_config_object_key: Mapped[str | None] = mapped_column(String(1024))
    encrypted_config_ciphertext_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted_config_protected_value_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted_config_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    encrypted_config_encryption_key_id: Mapped[str | None] = mapped_column(String(256))
    encrypted_config_schema_version: Mapped[str | None] = mapped_column(String(32))
    runtime_lock_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_execution_observation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    connector_connection_selections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    mcp_connection_selections_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    ingress_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_name: Mapped[str] = mapped_column(String(256), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    next_attempt_fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recovery_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    max_recovery_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_handoffs: Mapped[int] = mapped_column(Integer, nullable=False)
    recovery_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    attempts_started: Mapped[int] = mapped_column(Integer, nullable=False)
    recovery_attempts_started: Mapped[int] = mapped_column(Integer, nullable=False)
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
            "id": self.id,
            "version": self.version,
            "tenant_id": self.tenant_id,
            "authority_principal": PrincipalRef(
                principal_type=PrincipalType(self.authority_principal_type),
                principal_id=self.authority_principal_id,
            ),
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "parent_run_id": self.parent_run_id,
            "retry_of_run_id": self.retry_of_run_id,
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
            "encrypted_config_payload": self._encrypted_config_ref(),
            "runtime_lock_digest": self.runtime_lock_digest,
            "model_execution_observation": _MODEL_OBSERVATION_ADAPTER.validate_python(
                self.model_execution_observation_json
            ),
            "connector_connection_selections": _JSON_OBJECTS_ADAPTER.validate_python(
                self.connector_connection_selections_json
            ),
            "mcp_connection_selections": _JSON_OBJECTS_ADAPTER.validate_python(self.mcp_connection_selections_json),
            "ingress_context": _JSON_OBJECT_ADAPTER.validate_python(self.ingress_context_json),
            "priority": self.priority,
            "queue_name": self.queue_name,
            "available_at": assume_utc(self.available_at),
            "current_run_attempt_id": self.current_run_attempt_id,
            "next_attempt_fence": self.next_attempt_fence,
            "recovery_budget": RecoveryBudget(
                policy_version=self.recovery_policy_version,
                max_recovery_attempts=self.max_recovery_attempts,
                max_handoffs=self.max_handoffs,
                recovery_deadline_at=optional_assume_utc(self.recovery_deadline_at),
                max_usage=_RECOVERY_LIMIT_ADAPTER.validate_python(self.max_usage_json),
            ),
            "attempts_started": self.attempts_started,
            "recovery_attempts_started": self.recovery_attempts_started,
            "handoffs_completed": self.handoffs_completed,
            "usage_charged": _RECOVERY_USAGE_ADAPTER.validate_python(self.usage_charged_json),
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

    def _encrypted_config_ref(self) -> EncryptedRunConfigPayloadRef | None:
        if self.encrypted_config_object_key is None:
            return None
        return _ENCRYPTED_CONFIG_REF_ADAPTER.validate_python(
            {
                "object_key": self.encrypted_config_object_key,
                "ciphertext_digest_sha256": self.encrypted_config_ciphertext_digest_sha256,
                "protected_value_digest_sha256": self.encrypted_config_protected_value_digest_sha256,
                "size_bytes": self.encrypted_config_size_bytes,
                "encryption_key_id": self.encrypted_config_encryption_key_id,
                "schema_version": self.encrypted_config_schema_version,
            }
        )

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
            ("tenant_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "run_id", "replaces_run_attempt_id"),
            ("run_attempts.tenant_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("fence >= 1", name="fence_positive"),
        CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "yield_reason IS NULL OR yield_reason IN ('service_drain', 'runner_rotation')",
            name="yield_reason_valid",
        ),
        CheckConstraint("length(runtime_lock_digest) = 64", name="runtime_lock_digest_sha256"),
        CheckConstraint("length(lease_token_digest) = 64", name="lease_token_digest_sha256"),
        CheckConstraint("length(worker_id) BETWEEN 1 AND 256", name="worker_id_bounded"),
        CheckConstraint("length(worker_generation) BETWEEN 1 AND 256", name="worker_generation_bounded"),
        CheckConstraint("length(worker_build_id) BETWEEN 1 AND 256", name="worker_build_id_bounded"),
        CheckConstraint(
            "(status IN ('succeeded', 'yielded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="finished_at_lifecycle_valid",
        ),
        CheckConstraint(
            "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
            "OR (status IN ('running', 'succeeded') AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
            "OR (status IN ('yielded', 'failed', 'cancelled') "
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
        Index("uq_run_attempts_tenant_id", "tenant_id", "id", unique=True),
        UniqueConstraint("tenant_id", "run_id", "id", name="uq_run_attempts_run_id"),
        Index(
            "uq_run_attempts_generation_identity",
            "tenant_id",
            "run_id",
            "id",
            "fence",
            unique=True,
        ),
        Index("uq_run_attempts_number", "tenant_id", "run_id", "attempt_number", unique=True),
        Index("uq_run_attempts_fence", "tenant_id", "run_id", "fence", unique=True),
        Index(
            "ix_run_attempts_live_lease",
            "tenant_id",
            "status",
            "lease_expires_at",
            "run_id",
            postgresql_where=text("status IN ('leased', 'running')"),
            sqlite_where=text("status IN ('leased', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    replaces_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    recovery_reason: Mapped[str | None] = mapped_column(String(256))
    worker_id: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_generation: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_build_id: Mapped[str] = mapped_column(String(256), nullable=False)
    runtime_lock_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    harness_run_id: Mapped[str | None] = mapped_column(String(256))
    model_execution_observation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    lease_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    yield_reason: Mapped[str | None] = mapped_column(String(32))
    failure_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> RunAttempt:
        return RunAttempt(
            id=self.id,
            version=self.version,
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            fence=self.fence,
            status=RunAttemptStatus(self.status),
            replaces_run_attempt_id=self.replaces_run_attempt_id,
            recovery_reason=self.recovery_reason,
            worker_id=self.worker_id,
            worker_generation=self.worker_generation,
            worker_build_id=self.worker_build_id,
            runtime_lock_digest=self.runtime_lock_digest,
            harness_run_id=self.harness_run_id,
            model_execution_observation=_MODEL_OBSERVATION_ADAPTER.validate_python(
                self.model_execution_observation_json
            ),
            lease_token_digest=self.lease_token_digest,
            lease_expires_at=assume_utc(self.lease_expires_at),
            heartbeat_at=assume_utc(self.heartbeat_at),
            usage=_RECOVERY_USAGE_ADAPTER.validate_python(self.usage_json),
            yield_reason=None if self.yield_reason is None else RunAttemptYieldReason(self.yield_reason),
            failure=_FAILURE_ADAPTER.validate_python(self.failure_json),
            created_at=assume_utc(self.created_at),
            claimed_at=assume_utc(self.claimed_at),
            started_at=optional_assume_utc(self.started_at),
            finished_at=optional_assume_utc(self.finished_at),
            updated_at=assume_utc(self.updated_at),
        )


def _json_null(value: Any) -> Any:
    return None if value is JSON.NULL else value


__all__ = ["RunAttemptRecord", "RunRecord", "SessionRecord", "ThreadRecord"]
