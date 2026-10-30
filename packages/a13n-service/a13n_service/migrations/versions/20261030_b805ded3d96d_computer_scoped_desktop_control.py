"""computer scoped desktop control

Revision ID: b805ded3d96d
Revises: 29270d0290da
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b805ded3d96d"
down_revision = "29270d0290da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This cutover requires drained old workers and verified revocation on the old release.
    # Lock before checking: a concurrent legacy writer must not create a grant after preflight.
    op.execute(
        "LOCK TABLE threads, runs, desktop_targets, desktop_assistance_requests, "
        "desktop_viewers, desktop_executions IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM desktop_assistance_requests
                WHERE grant_id IS NOT NULL OR controller_id IS NOT NULL
                   OR operation_id IS NOT NULL OR lease_expires_at IS NOT NULL
                   OR state IN ('preparing', 'claiming', 'human_control', 'revoking', 'blocked')
            ) OR EXISTS (SELECT 1 FROM desktop_viewers WHERE NOT revoked)
              OR EXISTS (SELECT 1 FROM desktop_executions WHERE inflight > 0 OR uncertain)
              OR EXISTS (SELECT 1 FROM desktop_targets WHERE inflight > 0)
              OR EXISTS (
                SELECT 1 FROM threads t JOIN runs r ON r.id = t.last_run_id
                WHERE t.current_run_id IS NULL AND r.status = 'waiting' AND (
                    r.pending->>'desktop_control_id' IS NOT NULL
                    OR jsonb_path_exists(r.pending, '$.calls[*] ? (@.tool_name == "request_desktop_assistance")')
                    OR jsonb_path_exists(r.pending, '$.approvals[*] ? (@.tool_name == "request_desktop_assistance")')
                )
              ) THEN
                RAISE EXCEPTION 'Desktop cutover requires verified legacy grant revocation, drained operations, '
                    'and completion or interruption of current desktop waits on the old release';
            END IF;
        END $$
    """)
    op.drop_constraint(op.f("ck_inbox_entries_kind"), "inbox_entries", type_="check")
    op.create_check_constraint(
        op.f("ck_inbox_entries_kind"),
        "inbox_entries",
        "kind IN ('message', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback')",
    )
    op.drop_constraint(op.f("ck_runs_trigger"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_trigger"),
        "runs",
        "trigger IN ('input', 'queued', 'resume', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback', 'spawned')",
    )
    op.drop_constraint(op.f("ck_outbox_kind"), "outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_kind"),
        "outbox",
        "kind IN ('webhook', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback', 'email', 'memory_purge', 'checkpoint_cleanup')",
    )
    op.create_table(
        "desktop_controls",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("episode", sa.BigInteger(), nullable=False),
        sa.Column("control", sa.String(), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=True),
        sa.Column("provider_identity", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("controller_id", sa.String(length=72), nullable=True),
        sa.Column("controller_thread_id", sa.String(length=72), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_generation", sa.String(), nullable=True),
        sa.Column("grant_id", sa.String(), nullable=True),
        sa.Column("grant_dispatched", sa.Boolean(), nullable=False),
        sa.Column("viewer", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("claim_id", sa.String(), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure", sa.String(), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "control IN ('automation','draining','human','returning')", name=op.f("ck_desktop_controls_control")
        ),
        sa.CheckConstraint("(claim_id IS NULL) = (claim_until IS NULL)", name=op.f("ck_desktop_controls_claim")),
        sa.CheckConstraint("generation > 0 AND episode >= 0", name=op.f("ck_desktop_controls_generation")),
        sa.ForeignKeyConstraint(
            ["controller_id"], ["principals.id"], name=op.f("fk_desktop_controls_controller_id_principals")
        ),
        sa.ForeignKeyConstraint(
            ["controller_thread_id"], ["threads.id"], name=op.f("fk_desktop_controls_controller_thread_id_threads")
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], name=op.f("fk_desktop_controls_environment_id_environments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_desktop_controls")),
        sa.UniqueConstraint("target_key", name=op.f("uq_desktop_controls_target_key")),
    )
    op.create_index(
        "ix_desktop_controls_due",
        "desktop_controls",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("control <> 'automation'"),
    )
    op.create_table(
        "desktop_blocked_threads",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("control_id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("mount", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("entry_id", sa.String(length=72), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["control_id"], ["desktop_controls.id"], name=op.f("fk_desktop_blocked_threads_control_id_desktop_controls")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["inbox_entries.id"], name=op.f("fk_desktop_blocked_threads_entry_id_inbox_entries")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_desktop_blocked_threads_run_id_runs")),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["threads.id"], name=op.f("fk_desktop_blocked_threads_thread_id_threads")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_desktop_blocked_threads")),
        sa.UniqueConstraint(
            "control_id",
            "generation",
            "episode",
            "thread_id",
            name=op.f("uq_desktop_blocked_threads_control_id_generation_episode_thread_id"),
        ),
    )
    op.create_table(
        "desktop_readers",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("control_id", sa.String(length=72), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("principal_id", sa.String(length=72), nullable=False),
        sa.Column("guest_generation", sa.String(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_id"], ["desktop_controls.id"], name=op.f("fk_desktop_readers_control_id_desktop_controls")
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], name=op.f("fk_desktop_readers_environment_id_environments")
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principals.id"], name=op.f("fk_desktop_readers_principal_id_principals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_desktop_readers")),
    )
    op.create_table(
        "computer_operations",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("control_id", sa.String(length=72), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("execution_id", sa.String(length=72), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("dispatched", sa.Boolean(), nullable=True),
        sa.Column("effect", sa.String(), nullable=True),
        sa.Column("cleanup_complete", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["run_attempts.id"], name=op.f("fk_computer_operations_attempt_id_run_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["control_id"], ["desktop_controls.id"], name=op.f("fk_computer_operations_control_id_desktop_controls")
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], name=op.f("fk_computer_operations_environment_id_environments")
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["tool_executions.id"], name=op.f("fk_computer_operations_execution_id_tool_executions")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_computer_operations_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_computer_operations")),
    )
    op.create_index(
        "ix_computer_operations_unsettled",
        "computer_operations",
        ["control_id"],
        unique=False,
        postgresql_where=sa.text("settled_at IS NULL"),
    )
    op.drop_table("desktop_executions")
    op.drop_index(op.f("ix_desktop_viewers_assistance"), table_name="desktop_viewers")
    op.drop_table("desktop_viewers")
    op.drop_index(op.f("ix_desktop_assistance_due"), table_name="desktop_assistance_requests")
    op.drop_index(
        op.f("uq_desktop_user_control"),
        table_name="desktop_assistance_requests",
        postgresql_where="((cause)::text = 'user'::text)",
    )
    op.drop_table("desktop_assistance_requests")
    op.drop_table("desktop_targets")
    op.execute(
        """
    CREATE TRIGGER stamp_resource BEFORE UPDATE ON desktop_controls FOR EACH ROW WHEN ((to_jsonb(OLD) - '{due_at,claim_id,claim_until}'::text[]) IS DISTINCT FROM (to_jsonb(NEW) - '{due_at,claim_id,claim_until}'::text[])) EXECUTE FUNCTION stamp_resource()
    """
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.execute(
        "LOCK TABLE desktop_controls, computer_operations, desktop_blocked_threads, desktop_readers, "
        "inbox_entries, runs, outbox IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM desktop_controls)
              OR EXISTS (SELECT 1 FROM inbox_entries WHERE kind = 'desktop_handback')
              OR EXISTS (SELECT 1 FROM outbox WHERE kind = 'desktop_handback')
              OR EXISTS (SELECT 1 FROM runs WHERE trigger = 'desktop_handback') THEN
                RAISE EXCEPTION 'Cannot downgrade Computer-scoped desktop data; use application rollback or forward repair';
            END IF;
        END $$
    """)
    op.drop_constraint(op.f("ck_inbox_entries_kind"), "inbox_entries", type_="check")
    op.create_check_constraint(
        op.f("ck_inbox_entries_kind"),
        "inbox_entries",
        "kind IN ('message', 'child_result', 'tool_result', 'browser_handback')",
    )
    op.drop_constraint(op.f("ck_runs_trigger"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_trigger"),
        "runs",
        "trigger IN ('input', 'queued', 'resume', 'child_result', 'tool_result', 'browser_handback', 'spawned')",
    )
    op.drop_constraint(op.f("ck_outbox_kind"), "outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_kind"),
        "outbox",
        "kind IN ('webhook', 'child_result', 'tool_result', 'browser_handback', 'email', 'memory_purge', 'checkpoint_cleanup')",
    )
    op.create_table(
        "desktop_targets",
        sa.Column("key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("holder_id", sa.VARCHAR(length=72), autoincrement=False, nullable=True),
        sa.Column("epoch", sa.BIGINT(), server_default=sa.text("0"), autoincrement=False, nullable=False),
        sa.Column("inflight", sa.BIGINT(), server_default=sa.text("0"), autoincrement=False, nullable=False),
        sa.Column(
            "unmanaged_input", sa.BOOLEAN(), server_default=sa.text("false"), autoincrement=False, nullable=False
        ),
        sa.CheckConstraint("inflight >= 0", name=op.f("ck_desktop_targets_inflight")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_desktop_targets")),
    )
    op.create_table(
        "desktop_assistance_requests",
        sa.Column("id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("run_id", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("tool_call_id", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("environment_id", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("target_key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("mount", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("reason", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("state", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("generation", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("epoch", sa.BIGINT(), server_default=sa.text("0"), autoincrement=False, nullable=False),
        sa.Column("controller_id", sa.VARCHAR(length=72), autoincrement=False, nullable=True),
        sa.Column("controller_session", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("grant_id", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("deadline", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column("lease_expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
        sa.Column("operation_id", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("operation_expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column("failure", sa.VARCHAR(), autoincrement=False, nullable=True),
        sa.Column("successor_id", sa.VARCHAR(length=72), autoincrement=False, nullable=True),
        sa.Column("observed", sa.BOOLEAN(), server_default=sa.text("false"), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "cause",
            sa.VARCHAR(length=16),
            server_default=sa.text("'agent'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "cause::text = ANY (ARRAY['agent'::character varying, 'user'::character varying]::text[])",
            name=op.f("ck_desktop_assistance_requests_cause"),
        ),
        sa.CheckConstraint(
            "state::text = ANY (ARRAY['viewing'::character varying, 'preparing'::character varying, 'awaiting_user'::character varying, 'claiming'::character varying, 'human_control'::character varying, 'revoking'::character varying, 'ready_to_resume'::character varying, 'consumed'::character varying, 'blocked'::character varying, 'abandoned'::character varying]::text[])",
            name=op.f("ck_desktop_assistance_requests_state"),
        ),
        sa.ForeignKeyConstraint(
            ["controller_id"], ["principals.id"], name=op.f("fk_desktop_assistance_requests_controller_id_principals")
        ),
        sa.ForeignKeyConstraint(
            ["successor_id"], ["runs.id"], name=op.f("fk_desktop_assistance_requests_successor_id_runs")
        ),
        sa.ForeignKeyConstraint(
            ["target_key"],
            ["desktop_targets.key"],
            name=op.f("fk_desktop_assistance_requests_target_key_desktop_targets"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.id"],
            name=op.f("fk_desktop_assistance_requests_workspace_id_environments"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["runs.workspace_id", "runs.id"],
            name=op.f("fk_desktop_assistance_requests_workspace_id_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_desktop_assistance_requests_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_desktop_assistance_requests")),
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name=op.f("uq_desktop_assistance_requests_run_id_tool_call_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("uq_desktop_user_control"),
        "desktop_assistance_requests",
        ["run_id"],
        unique=True,
        postgresql_where="((cause)::text = 'user'::text)",
    )
    op.create_index(
        op.f("ix_desktop_assistance_due"), "desktop_assistance_requests", ["deadline", "state"], unique=False
    )
    op.create_table(
        "desktop_viewers",
        sa.Column("id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("assistance_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("principal_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("session_id", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("generation", sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column("epoch", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column("revoked", sa.BOOLEAN(), server_default=sa.text("false"), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["assistance_id"],
            ["desktop_assistance_requests.id"],
            name=op.f("fk_desktop_viewers_assistance_id_desktop_assistance_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principals.id"], name=op.f("fk_desktop_viewers_principal_id_principals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_desktop_viewers")),
    )
    op.create_index(op.f("ix_desktop_viewers_assistance"), "desktop_viewers", ["assistance_id"], unique=False)
    op.create_table(
        "desktop_executions",
        sa.Column("run_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("attempt_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("supported", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("inflight", sa.BIGINT(), server_default=sa.text("0"), autoincrement=False, nullable=False),
        sa.Column("uncertain", sa.BOOLEAN(), server_default=sa.text("false"), autoincrement=False, nullable=False),
        sa.CheckConstraint("inflight >= 0", name=op.f("ck_desktop_executions_inflight")),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["run_attempts.id"], name=op.f("fk_desktop_executions_attempt_id_run_attempts")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_desktop_executions_run_id_runs")),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_desktop_executions")),
    )
    op.drop_index(
        "ix_computer_operations_unsettled",
        table_name="computer_operations",
        postgresql_where=sa.text("settled_at IS NULL"),
    )
    op.drop_table("computer_operations")
    op.drop_table("desktop_readers")
    op.drop_table("desktop_blocked_threads")
    op.drop_index(
        "ix_desktop_controls_due", table_name="desktop_controls", postgresql_where=sa.text("control <> 'automation'")
    )
    op.drop_table("desktop_controls")
    # ### end Alembic commands ###
