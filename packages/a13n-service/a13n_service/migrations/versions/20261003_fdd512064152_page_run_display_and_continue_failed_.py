"""page run display and continue failed runs

Revision ID: fdd512064152
Revises: 21af2571a2be
"""

import sqlalchemy as sa
from alembic import op

revision = "fdd512064152"
down_revision = "21af2571a2be"
branch_labels = None
depends_on = None


# Rendered from the table rules: each upgrade and downgrade replaces both functions with its own revision's text.
GUARD_RUN = """
CREATE OR REPLACE FUNCTION guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('waiting', 'completed', 'failed', 'cancelled') THEN
        -- Labels are the one editable property of a sealed run.
        IF (to_jsonb(NEW) - ARRAY['labels', 'version', 'updated_at'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['labels', 'version', 'updated_at'])
        THEN RAISE EXCEPTION 'sealed run facts are immutable'; END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'tail', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'tail', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at'])
    THEN RAISE EXCEPTION 'accepted run selection is immutable'; END IF;
    IF NEW.status <> OLD.status AND NOT (
        (OLD.status = 'accepted' AND NEW.status IN ('running', 'failed', 'cancelled'))
        OR (OLD.status = 'running' AND NEW.status IN ('accepted', 'waiting', 'completed', 'failed',
                                                       'cancelled'))
    ) THEN RAISE EXCEPTION 'invalid run transition % to %', OLD.status, NEW.status; END IF;
    RETURN NEW;
END $$
"""
PREVIOUS_GUARD_RUN = """
CREATE OR REPLACE FUNCTION guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('waiting', 'completed', 'failed', 'cancelled') THEN
        -- Labels are the one editable property of a sealed run.
        IF (to_jsonb(NEW) - ARRAY['labels', 'version', 'updated_at'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['labels', 'version', 'updated_at'])
        THEN RAISE EXCEPTION 'sealed run facts are immutable'; END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'display', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'display', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at'])
    THEN RAISE EXCEPTION 'accepted run selection is immutable'; END IF;
    IF NEW.status <> OLD.status AND NOT (
        (OLD.status = 'accepted' AND NEW.status IN ('running', 'failed', 'cancelled'))
        OR (OLD.status = 'running' AND NEW.status IN ('accepted', 'waiting', 'completed', 'failed',
                                                       'cancelled'))
    ) THEN RAISE EXCEPTION 'invalid run transition % to %', OLD.status, NEW.status; END IF;
    RETURN NEW;
END $$
"""
CHECK_THREAD_POINTERS = """
CREATE OR REPLACE FUNCTION check_thread_pointers() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t threads; target text;
BEGIN
    IF TG_TABLE_NAME = 'threads' THEN target := NEW.id; ELSE target := NEW.thread_id; END IF;
    SELECT * INTO t FROM threads WHERE id = target;
    IF t.current_run_id IS DISTINCT FROM (
            SELECT id FROM runs WHERE thread_id = target AND status IN ('accepted', 'running'))
        OR (t.last_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM runs WHERE id = t.last_run_id AND sealed_at IS NOT NULL))
    THEN RAISE EXCEPTION 'thread % run pointers disagree with run status', target; END IF;
    RETURN NULL;
END $$
"""
PREVIOUS_CHECK_THREAD_POINTERS = """
CREATE OR REPLACE FUNCTION check_thread_pointers() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t threads; target text;
BEGIN
    IF TG_TABLE_NAME = 'threads' THEN target := NEW.id; ELSE target := NEW.thread_id; END IF;
    SELECT * INTO t FROM threads WHERE id = target;
    IF t.current_run_id IS DISTINCT FROM (
            SELECT id FROM runs WHERE thread_id = target AND status IN ('accepted', 'running'))
        OR (t.head_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM runs WHERE id = t.head_run_id AND status IN ('completed', 'waiting')))
        OR (t.last_run_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM runs WHERE id = t.last_run_id AND sealed_at IS NOT NULL))
    THEN RAISE EXCEPTION 'thread % run pointers disagree with run status', target; END IF;
    RETURN NULL;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "run_item_pages",
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("first_ordinal", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("last_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "first_ordinal >= 1 AND last_ordinal >= first_ordinal", name=op.f("ck_run_item_pages_ordinals")
        ),
        sa.CheckConstraint("size >= 0", name=op.f("ck_run_item_pages_size")),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_run_item_pages_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_run_item_pages_organization_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["runs.workspace_id", "runs.id"],
            name=op.f("fk_run_item_pages_workspace_id_runs"),
        ),
        sa.PrimaryKeyConstraint("run_id", "first_ordinal", name=op.f("pk_run_item_pages")),
    )
    op.execute(
        """
    CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE ON run_item_pages FOR EACH ROW EXECUTE FUNCTION refuse_mutation()
    """
    )
    # The display pointer now names the tail; renaming keeps the outcome_state check that covers it.
    op.alter_column("runs", "display", new_column_name="tail")
    op.execute(GUARD_RUN)
    # Every sealed run is the thread's head, so the last run pointer replaces the head pointer.
    op.execute(CHECK_THREAD_POINTERS)
    op.drop_index("ix_threads_advanceable", table_name="threads")
    op.drop_constraint(op.f("fk_threads_head_run_id"), "threads", type_="foreignkey")
    op.drop_column("threads", "head_run_id")
    op.create_index(
        "ix_threads_advanceable",
        "threads",
        ["id"],
        unique=False,
        postgresql_where=sa.text("current_run_id IS NULL AND archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_threads_advanceable", table_name="threads")
    op.add_column("threads", sa.Column("head_run_id", sa.VARCHAR(length=72), autoincrement=False, nullable=True))
    op.create_foreign_key(
        op.f("fk_threads_head_run_id"),
        "threads",
        "runs",
        ["id", "head_run_id"],
        ["thread_id", "id"],
        initially="DEFERRED",
        deferrable=True,
    )
    op.create_index(
        "ix_threads_advanceable",
        "threads",
        ["id"],
        unique=False,
        postgresql_where=sa.text(
            "current_run_id IS NULL AND archived_at IS NULL AND last_run_id IS NOT DISTINCT FROM head_run_id"
        ),
    )
    op.execute(PREVIOUS_CHECK_THREAD_POINTERS)
    op.alter_column("runs", "tail", new_column_name="display")
    op.execute(PREVIOUS_GUARD_RUN)
    op.drop_table("run_item_pages")
