"""initialize interaction execution.

Revision ID: 7de20ce04aa6
Revises: 568f8270be7a
Create Date: 2026-09-04 08:21:30.895851+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7de20ce04aa6"
down_revision: str | Sequence[str] | None = "568f8270be7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_sessions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("organization_id", "id", name="uq_sessions_organization_id"),
    )
    op.create_index(
        "ix_sessions_workspace_created",
        "sessions",
        ["organization_id", "workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_workspace_updated",
        "sessions",
        ["organization_id", "workspace_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_labels",
        "sessions",
        ["labels"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"labels": "jsonb_path_ops"},
    )
    op.create_table(
        "threads",
        sa.Column("default_environment_id", sa.String(length=72), nullable=True),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("queue_version", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("session_id", sa.String(length=72), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("origin_kind", sa.String(length=16), nullable=False),
        sa.Column("origin_thread_id", sa.String(length=72), nullable=True),
        sa.Column("origin_run_id", sa.String(length=72), nullable=True),
        sa.Column("head_run_id", sa.String(length=72), nullable=True),
        sa.Column("current_run_id", sa.String(length=72), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(origin_kind = 'new' AND role = 'root' AND origin_thread_id IS NULL AND origin_run_id IS NULL) OR (origin_kind = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL) OR (origin_kind = 'child' AND role = 'child' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL)",
            name=op.f("ck_threads_origin_shape_valid"),
        ),
        sa.CheckConstraint("origin_kind IN ('new', 'fork', 'child')", name=op.f("ck_threads_origin_kind_valid")),
        sa.CheckConstraint("role IN ('root', 'child')", name=op.f("ck_threads_role_valid")),
        sa.CheckConstraint("queue_version >= 0", name=op.f("ck_threads_queue_version_non_negative")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_threads_version_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "origin_thread_id", "origin_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_threads_origin_run_same_thread",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "origin_thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_threads_organization_id_threads"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id", "id", "current_run_id"],
            ["runs.organization_id", "runs.session_id", "runs.thread_id", "runs.id"],
            name="fk_threads_current_run_same_thread",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id", "id", "head_run_id"],
            ["runs.organization_id", "runs.session_id", "runs.thread_id", "runs.id"],
            name="fk_threads_head_run_same_thread",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["sessions.organization_id", "sessions.id"],
            name=op.f("fk_threads_organization_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["default_environment_id"],
            ["environments.id"],
            name="fk_threads_default_environment_id_environments",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_threads")),
        sa.UniqueConstraint("organization_id", "id", name="uq_threads_organization_id"),
        sa.UniqueConstraint("organization_id", "session_id", "id", name="uq_threads_session_id"),
    )
    op.create_index(
        "ix_threads_selected_run",
        "threads",
        ["organization_id", sa.text("coalesce(current_run_id, head_run_id)")],
        unique=False,
    )
    op.create_index("ix_threads_origin_run", "threads", ["organization_id", "origin_run_id", "id"], unique=False)
    op.create_index("ix_threads_origin_thread", "threads", ["organization_id", "origin_thread_id", "id"], unique=False)
    op.create_index(
        "ix_threads_session_created", "threads", ["organization_id", "session_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_threads_session_updated", "threads", ["organization_id", "session_id", "updated_at", "id"], unique=False
    )
    op.create_index(
        "ix_threads_labels",
        "threads",
        ["labels"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"labels": "jsonb_path_ops"},
    )
    op.create_index(
        "uq_threads_session_root",
        "threads",
        ["organization_id", "session_id"],
        unique=True,
        postgresql_where=sa.text("role = 'root'"),
    )
    op.create_table(
        "runs",
        sa.Column("environment_id", sa.String(length=72), nullable=True),
        sa.Column("environment_access", sa.String(length=16), nullable=True),
        sa.Column("environment_use_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("authority_principal_type", sa.String(length=32), nullable=False),
        sa.Column("authority_principal_id", sa.String(length=72), nullable=False),
        sa.Column("session_id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("parent_run_id", sa.String(length=72), nullable=True),
        sa.Column("retry_of_run_id", sa.String(length=72), nullable=True),
        sa.Column("lineage_kind", sa.String(length=16), nullable=False),
        sa.Column("trigger_type", sa.String(length=256), nullable=False),
        sa.Column("trigger_entity_type", sa.String(length=256), nullable=True),
        sa.Column("trigger_entity_id", sa.String(length=256), nullable=True),
        sa.Column("parent_agent_instance_id", sa.String(length=256), nullable=True),
        sa.Column("delegation_id", sa.String(length=256), nullable=True),
        sa.Column("parent_tool_call_id", sa.String(length=256), nullable=True),
        sa.Column("agent_id", sa.String(length=72), nullable=False),
        sa.Column("agent_revision_id", sa.String(length=72), nullable=False),
        sa.Column("effective_agent_config_digest", sa.String(length=64), nullable=False),
        sa.Column("model_execution_observation_json", sa.JSON(), nullable=False),
        sa.Column("connection_selections_json", sa.JSON(), nullable=False),
        sa.Column("native_tool_contexts_json", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("queue_name", sa.String(length=256), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_run_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("execution_policy_version", sa.String(length=32), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_handoffs", sa.Integer(), nullable=False),
        sa.Column("execution_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_usage_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("attempts_started", sa.Integer(), nullable=False),
        sa.Column("attempts_charged", sa.Integer(), nullable=False),
        sa.Column("handoffs_completed", sa.Integer(), nullable=False),
        sa.Column("usage_charged_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("wait_reason", sa.String(length=16), nullable=True),
        sa.Column("pending_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("input_kind", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("input_object_key", sa.String(length=1024), nullable=True),
        sa.Column("input_object_digest_sha256", sa.String(length=64), nullable=True),
        sa.Column("input_object_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("input_object_content_type", sa.String(length=255), nullable=True),
        sa.Column("input_object_schema_version", sa.String(length=32), nullable=True),
        sa.Column("input_text", sa.String(length=65536), nullable=True),
        sa.Column("output_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("output_object_key", sa.String(length=1024), nullable=True),
        sa.Column("output_object_digest_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_object_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("output_object_content_type", sa.String(length=255), nullable=True),
        sa.Column("output_object_schema_version", sa.String(length=32), nullable=True),
        sa.Column("output_text", sa.String(length=65536), nullable=True),
        sa.Column("failure_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("sealed_state_digest_sha256", sa.String(length=64), nullable=True),
        sa.Column("sealed_state_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sealed_state_content_type", sa.String(length=255), nullable=True),
        sa.Column("sealed_state_envelope_schema_version", sa.String(length=32), nullable=True),
        sa.Column("sealed_state_harness_schema_version", sa.String(length=32), nullable=True),
        sa.Column("sealed_state_checkpoint_seq", sa.BigInteger(), nullable=True),
        sa.Column("sealed_state_committed_by_run_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(lineage_kind = 'root' AND parent_run_id IS NULL) OR (lineage_kind IN ('continue', 'fork') AND parent_run_id IS NOT NULL)",
            name=op.f("ck_runs_lineage_shape_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND current_run_attempt_id IS NULL AND attempts_started = 0) OR (status = 'running' AND attempts_started >= 1) OR (status IN ('waiting', 'completed', 'failed', 'cancelled') AND current_run_attempt_id IS NULL)",
            name=op.f("ck_runs_current_attempt_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND (output_json IS NOT NULL) <> (output_object_key IS NOT NULL) AND completed_at IS NOT NULL) OR (status <> 'completed' AND output_json IS NULL AND output_object_key IS NULL AND output_text IS NULL AND completed_at IS NULL)",
            name=op.f("ck_runs_output_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'waiting' AND wait_reason IS NOT NULL AND pending_json IS NOT NULL AND waiting_at IS NOT NULL) OR (status <> 'waiting' AND wait_reason IS NULL AND pending_json IS NULL AND waiting_at IS NULL)",
            name=op.f("ck_runs_waiting_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('failed', 'cancelled') AND failure_json IS NOT NULL) OR (status NOT IN ('failed', 'cancelled') AND failure_json IS NULL)",
            name=op.f("ck_runs_failure_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('waiting', 'completed') AND sealed_state_digest_sha256 IS NOT NULL AND sealed_state_checkpoint_seq > 0 AND sealed_state_committed_by_run_attempt_id IS NOT NULL) OR (status IN ('accepted', 'running', 'cancelled') AND sealed_state_digest_sha256 IS NULL) OR status = 'failed'",
            name=op.f("ck_runs_sealed_state_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('waiting', 'completed', 'failed', 'cancelled')) = (sealed_at IS NOT NULL)",
            name=op.f("ck_runs_sealed_at_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "authority_principal_type IN ('user', 'service_account')", name=op.f("ck_runs_principal_type_valid")
        ),
        sa.CheckConstraint(
            "input_kind IN ('agent_input', 'waiting_feedback', 'waiting_continue', 'async_subagent_result')",
            name=op.f("ck_runs_input_kind_valid"),
        ),
        sa.CheckConstraint("lineage_kind IN ('root', 'continue', 'fork')", name=op.f("ck_runs_lineage_kind_valid")),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "wait_reason IS NULL OR wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')",
            name=op.f("ck_runs_wait_reason_valid"),
        ),
        sa.CheckConstraint(
            "(input_object_digest_sha256 IS NULL OR length(input_object_digest_sha256) = 64) AND (output_object_digest_sha256 IS NULL OR length(output_object_digest_sha256) = 64) AND (sealed_state_digest_sha256 IS NULL OR length(sealed_state_digest_sha256) = 64)",
            name=op.f("ck_runs_optional_digests_sha256"),
        ),
        sa.CheckConstraint(
            "(input_json IS NOT NULL) <> (input_object_key IS NOT NULL)",
            name=op.f("ck_runs_input_representation_valid"),
        ),
        sa.CheckConstraint(
            "(input_object_key IS NULL AND input_object_digest_sha256 IS NULL AND input_object_size_bytes IS NULL AND input_object_content_type IS NULL AND input_object_schema_version IS NULL) OR (input_object_key IS NOT NULL AND input_object_digest_sha256 IS NOT NULL AND input_object_size_bytes > 0 AND input_object_content_type IS NOT NULL AND input_object_schema_version IS NOT NULL)",
            name=op.f("ck_runs_input_object_group_valid"),
        ),
        sa.CheckConstraint(
            "(output_object_key IS NULL AND output_object_digest_sha256 IS NULL AND output_object_size_bytes IS NULL AND output_object_content_type IS NULL AND output_object_schema_version IS NULL) OR (output_object_key IS NOT NULL AND output_object_digest_sha256 IS NOT NULL AND output_object_size_bytes > 0 AND output_object_content_type IS NOT NULL AND output_object_schema_version IS NOT NULL)",
            name=op.f("ck_runs_output_object_group_valid"),
        ),
        sa.CheckConstraint(
            "(sealed_state_digest_sha256 IS NULL AND sealed_state_size_bytes IS NULL AND sealed_state_content_type IS NULL AND sealed_state_envelope_schema_version IS NULL AND sealed_state_harness_schema_version IS NULL AND sealed_state_checkpoint_seq IS NULL AND sealed_state_committed_by_run_attempt_id IS NULL) OR (sealed_state_digest_sha256 IS NOT NULL AND sealed_state_size_bytes > 0 AND sealed_state_content_type IS NOT NULL AND sealed_state_envelope_schema_version IS NOT NULL AND sealed_state_harness_schema_version IS NOT NULL AND sealed_state_checkpoint_seq >= 0)",
            name=op.f("ck_runs_sealed_state_group_valid"),
        ),
        sa.CheckConstraint(
            "length(effective_agent_config_digest) = 64", name=op.f("ck_runs_effective_config_digest_sha256")
        ),
        sa.CheckConstraint("length(request_fingerprint) = 64", name=op.f("ck_runs_request_fingerprint_sha256")),
        sa.CheckConstraint(
            "max_attempts >= 0 AND max_handoffs >= 0 AND attempts_started >= 0 AND attempts_charged >= 0 AND handoffs_completed >= 0",
            name=op.f("ck_runs_execution_values_non_negative"),
        ),
        sa.CheckConstraint(
            "attempts_charged <= max_attempts AND handoffs_completed <= max_handoffs AND attempts_charged <= attempts_started AND attempts_started <= attempts_charged + handoffs_completed",
            name=op.f("ck_runs_execution_counts_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_runs_version_positive")),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_runs_agent_id_agents"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["agent_revision_id"],
            ["agent_revisions.id"],
            name=op.f("fk_runs_agent_revision_id_agent_revisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "id", "current_run_attempt_id"],
            ["run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"],
            name="fk_runs_current_attempt_same_run",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "id", "sealed_state_committed_by_run_attempt_id"],
            ["run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"],
            name="fk_runs_sealed_attempt_same_run",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_runs_parent_same_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id", "thread_id"],
            ["threads.organization_id", "threads.session_id", "threads.id"],
            name=op.f("fk_runs_organization_id_threads"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "retry_of_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_runs_retry_same_thread",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], name="fk_runs_environment_id_environments"),
        sa.CheckConstraint(
            "(environment_id IS NULL AND environment_access IS NULL AND environment_use_started_at IS NULL) OR (environment_id IS NOT NULL AND environment_access IN ('read_only','read_write','full'))",
            name=op.f("ck_runs_environment_selection_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("organization_id", "id", name="uq_runs_organization_id"),
        sa.UniqueConstraint("organization_id", "session_id", "thread_id", "id", name="uq_runs_scope_identity"),
        sa.UniqueConstraint("organization_id", "thread_id", "id", name="uq_runs_organization_thread_id"),
    )
    op.create_index("ix_runs_environment_id", "runs", ["environment_id"], unique=False)
    op.create_index("ix_runs_agent_session", "runs", ["organization_id", "agent_id", "session_id", "id"], unique=False)
    op.create_index("ix_runs_status_session", "runs", ["organization_id", "status", "session_id", "id"], unique=False)
    op.create_index(
        "ix_runs_trigger_session", "runs", ["organization_id", "trigger_type", "session_id", "id"], unique=False
    )
    op.create_index("ix_runs_parent", "runs", ["organization_id", "parent_run_id", "id"], unique=False)
    op.create_index("ix_runs_retry", "runs", ["organization_id", "retry_of_run_id", "id"], unique=False)
    op.create_index(
        "ix_runs_session_created", "runs", ["organization_id", "session_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_runs_thread_created", "runs", ["organization_id", "thread_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_runs_labels",
        "runs",
        ["labels"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"labels": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_runs_worker_scan",
        "runs",
        ["organization_id", "queue_name", "status", "available_at", "priority", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'accepted' OR (status = 'running' AND current_run_attempt_id IS NULL)"),
    )
    op.create_index(
        "uq_runs_active_thread",
        "runs",
        ["organization_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('accepted', 'running')"),
    )
    op.create_index(
        "uq_runs_idempotency",
        "runs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_runs_live_root_thread",
        "runs",
        ["organization_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("parent_run_id IS NULL AND status IN ('accepted', 'running', 'waiting', 'completed')"),
    )
    op.create_index(
        "uq_runs_thread_authority",
        "runs",
        ["organization_id", "thread_id", "id", "authority_principal_type", "authority_principal_id"],
        unique=True,
    )
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("replaces_run_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("start_reason", sa.String(length=256), nullable=True),
        sa.Column("worker_id", sa.String(length=256), nullable=False),
        sa.Column("worker_build_id", sa.String(length=256), nullable=False),
        sa.Column("harness_run_id", sa.String(length=256), nullable=True),
        sa.Column("model_execution_observation_json", sa.JSON(), nullable=False),
        sa.Column("lease_token_digest", sa.String(length=64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("yield_reason", sa.String(length=32), nullable=True),
        sa.Column("failure_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'failed' AND failure_json IS NOT NULL) OR (status IN ('leased', 'running', 'succeeded', 'yielded') AND failure_json IS NULL) OR status = 'cancelled'",
            name=op.f("ck_run_attempts_failure_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) OR (status = 'running' AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) OR (status IN ('succeeded', 'yielded', 'failed', 'cancelled') AND ((harness_run_id IS NULL AND started_at IS NULL) OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))",
            name=op.f("ck_run_attempts_harness_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'yielded' AND yield_reason IS NOT NULL AND failure_json IS NULL) OR (status <> 'yielded' AND yield_reason IS NULL)",
            name=op.f("ck_run_attempts_yield_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'yielded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name=op.f("ck_run_attempts_finished_at_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')",
            name=op.f("ck_run_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "yield_reason IS NULL OR yield_reason = 'service_drain'",
            name=op.f("ck_run_attempts_yield_reason_valid"),
        ),
        sa.CheckConstraint("attempt_number >= 1", name=op.f("ck_run_attempts_attempt_number_positive")),
        sa.CheckConstraint("length(lease_token_digest) = 64", name=op.f("ck_run_attempts_lease_token_digest_sha256")),
        sa.CheckConstraint(
            "length(worker_build_id) BETWEEN 1 AND 256", name=op.f("ck_run_attempts_worker_build_id_bounded")
        ),
        sa.CheckConstraint("length(worker_id) BETWEEN 1 AND 256", name=op.f("ck_run_attempts_worker_id_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_run_attempts_version_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "replaces_run_attempt_id"],
            ["run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"],
            name=op.f("fk_run_attempts_organization_id_run_attempts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name=op.f("fk_run_attempts_organization_id_runs"),
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_attempts")),
        sa.UniqueConstraint("organization_id", "run_id", "id", name="uq_run_attempts_run_id"),
    )
    op.create_index(
        "ix_run_attempts_live_lease",
        "run_attempts",
        ["organization_id", "status", "lease_expires_at", "run_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('leased', 'running')"),
    )
    op.create_index(
        "uq_run_attempts_number", "run_attempts", ["organization_id", "run_id", "attempt_number"], unique=True
    )
    op.create_index("uq_run_attempts_organization_id", "run_attempts", ["organization_id", "id"], unique=True)
    op.create_foreign_key(
        "fk_threads_current_run_same_thread",
        "threads",
        "runs",
        ["organization_id", "session_id", "id", "current_run_id"],
        ["organization_id", "session_id", "thread_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_threads_head_run_same_thread",
        "threads",
        "runs",
        ["organization_id", "session_id", "id", "head_run_id"],
        ["organization_id", "session_id", "thread_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_threads_origin_run_same_thread",
        "threads",
        "runs",
        ["organization_id", "origin_thread_id", "origin_run_id"],
        ["organization_id", "thread_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_runs_current_attempt_same_run",
        "runs",
        "run_attempts",
        ["organization_id", "id", "current_run_attempt_id"],
        ["organization_id", "run_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_runs_sealed_attempt_same_run",
        "runs",
        "run_attempts",
        ["organization_id", "id", "sealed_state_committed_by_run_attempt_id"],
        ["organization_id", "run_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    _create_terminal_guards()


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    _drop_terminal_guards()
    op.drop_constraint("fk_runs_sealed_attempt_same_run", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_current_attempt_same_run", "runs", type_="foreignkey")
    op.drop_constraint("fk_threads_origin_run_same_thread", "threads", type_="foreignkey")
    op.drop_constraint("fk_threads_head_run_same_thread", "threads", type_="foreignkey")
    op.drop_constraint("fk_threads_current_run_same_thread", "threads", type_="foreignkey")
    op.drop_index("uq_run_attempts_organization_id", table_name="run_attempts")
    op.drop_index("uq_run_attempts_number", table_name="run_attempts")
    op.drop_index(
        "ix_run_attempts_live_lease",
        table_name="run_attempts",
        postgresql_where=sa.text("status IN ('leased', 'running')"),
    )
    op.drop_table("run_attempts")
    op.drop_index("uq_runs_thread_authority", table_name="runs")
    op.drop_index(
        "uq_runs_live_root_thread",
        table_name="runs",
        postgresql_where=sa.text("parent_run_id IS NULL AND status IN ('accepted', 'running', 'waiting', 'completed')"),
    )
    op.drop_index("uq_runs_idempotency", table_name="runs", postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.drop_index(
        "uq_runs_active_thread", table_name="runs", postgresql_where=sa.text("status IN ('accepted', 'running')")
    )
    op.drop_index(
        "ix_runs_worker_scan",
        table_name="runs",
        postgresql_where=sa.text("status = 'accepted' OR (status = 'running' AND current_run_attempt_id IS NULL)"),
    )
    op.drop_index("ix_runs_thread_created", table_name="runs")
    op.drop_index("ix_runs_trigger_session", table_name="runs")
    op.drop_index("ix_runs_status_session", table_name="runs")
    op.drop_index("ix_runs_agent_session", table_name="runs")
    op.drop_index("ix_runs_session_created", table_name="runs")
    op.drop_index("ix_runs_retry", table_name="runs")
    op.drop_index("ix_runs_parent", table_name="runs")
    op.drop_table("runs")
    op.drop_index("uq_threads_session_root", table_name="threads", postgresql_where=sa.text("role = 'root'"))
    op.drop_index("ix_threads_selected_run", table_name="threads")
    op.drop_index("ix_threads_session_updated", table_name="threads")
    op.drop_index("ix_threads_session_created", table_name="threads")
    op.drop_index("ix_threads_origin_thread", table_name="threads")
    op.drop_index("ix_threads_origin_run", table_name="threads")
    op.drop_table("threads")
    op.drop_index("ix_sessions_workspace_updated", table_name="sessions")
    op.drop_index("ix_sessions_workspace_created", table_name="sessions")
    op.drop_table("sessions")


def _create_terminal_guards() -> None:
    """Apply the schema change."""
    op.execute(
        """
        CREATE FUNCTION reject_terminal_interaction_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'runs'
               AND OLD.status IN ('waiting', 'completed', 'failed', 'cancelled')
               AND (to_jsonb(NEW) - 'labels' - 'updated_at')
                   IS DISTINCT FROM (to_jsonb(OLD) - 'labels' - 'updated_at') THEN
                RAISE EXCEPTION 'sealed Run rows are immutable';
            END IF;
            IF TG_TABLE_NAME = 'run_attempts'
               AND OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled') THEN
                RAISE EXCEPTION 'terminal RunAttempt rows are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_sealed_run_update
        BEFORE UPDATE ON runs
        FOR EACH ROW EXECUTE FUNCTION reject_terminal_interaction_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_terminal_run_attempt_update
        BEFORE UPDATE ON run_attempts
        FOR EACH ROW EXECUTE FUNCTION reject_terminal_interaction_update()
        """
    )


def _drop_terminal_guards() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.execute("DROP TRIGGER reject_terminal_run_attempt_update ON run_attempts")
    op.execute("DROP TRIGGER reject_sealed_run_update ON runs")
    op.execute("DROP FUNCTION reject_terminal_interaction_update()")
