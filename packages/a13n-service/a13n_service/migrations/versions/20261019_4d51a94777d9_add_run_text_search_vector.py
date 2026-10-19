"""add run text search vector

Revision ID: 4d51a94777d9
Revises: 2e9655a2d223
"""

import sqlalchemy as sa
from alembic import op

revision = "4d51a94777d9"
down_revision = "2e9655a2d223"
branch_labels = None
depends_on = None


GUARD_RUN = """
CREATE OR REPLACE FUNCTION guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE new_facts runs := NEW; old_facts runs := OLD;
            BEGIN
                -- Avoid serializing the large search vector on every lease/checkpoint update.
                new_facts.search_vector := NULL;
                old_facts.search_vector := NULL;
                IF OLD.status IN ('waiting', 'completed', 'failed', 'cancelled') THEN
                    -- Labels are the one editable property of a sealed run.
                    IF NEW.search_vector IS DISTINCT FROM OLD.search_vector
                        OR (to_jsonb(new_facts) - ARRAY['labels', 'version', 'updated_at'])
                        IS DISTINCT FROM (to_jsonb(old_facts) - ARRAY['labels', 'version', 'updated_at'])
                    THEN RAISE EXCEPTION 'sealed run facts are immutable'; END IF;
                    RETURN NEW;
                END IF;
                IF (to_jsonb(new_facts) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'tail', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at']) IS DISTINCT FROM (to_jsonb(old_facts) - ARRAY['status', 'wait_reason', 'pending', 'cancel_requested_at', 'current_attempt_id', 'available_at', 'attempts', 'checkpoint', 'tail', 'memory_cursors', 'output', 'failure', 'usage_at_seal', 'labels', 'started_at', 'sealed_at', 'version', 'updated_at'])
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
STAMP = """
CREATE TRIGGER stamp_resource BEFORE UPDATE ON runs FOR EACH ROW WHEN (ROW(OLD."id", OLD."organization_id", OLD."workspace_id", OLD."session_id", OLD."thread_id", OLD."agent_id", OLD."agent_revision_id", OLD."revision_selection", OLD."principal_id", OLD."authority", OLD."options", OLD."options_digest", OLD."mcp_headers", OLD."environment_mounts", OLD."memory_mounts", OLD."source_entry_id", OLD."resume", OLD."resumed_by_id", OLD."request_key", OLD."request_digest", OLD."trigger", OLD."lineage", OLD."parent_run_id", OLD."status", OLD."wait_reason", OLD."pending", OLD."cancel_requested_at", OLD."current_attempt_id", OLD."available_at", OLD."attempts", OLD."max_attempts", OLD."output", OLD."failure", OLD."usage_at_seal", OLD."labels", OLD."started_at", OLD."sealed_at", OLD."version", OLD."created_at", OLD."updated_at") IS DISTINCT FROM ROW(NEW."id", NEW."organization_id", NEW."workspace_id", NEW."session_id", NEW."thread_id", NEW."agent_id", NEW."agent_revision_id", NEW."revision_selection", NEW."principal_id", NEW."authority", NEW."options", NEW."options_digest", NEW."mcp_headers", NEW."environment_mounts", NEW."memory_mounts", NEW."source_entry_id", NEW."resume", NEW."resumed_by_id", NEW."request_key", NEW."request_digest", NEW."trigger", NEW."lineage", NEW."parent_run_id", NEW."status", NEW."wait_reason", NEW."pending", NEW."cancel_requested_at", NEW."current_attempt_id", NEW."available_at", NEW."attempts", NEW."max_attempts", NEW."output", NEW."failure", NEW."usage_at_seal", NEW."labels", NEW."started_at", NEW."sealed_at", NEW."version", NEW."created_at", NEW."updated_at")) EXECUTE FUNCTION stamp_resource()
"""
PREVIOUS_STAMP = """
CREATE TRIGGER stamp_resource BEFORE UPDATE ON runs FOR EACH ROW WHEN ((to_jsonb(OLD) - '{checkpoint,tail,memory_cursors}'::text[]) IS DISTINCT FROM (to_jsonb(NEW) - '{checkpoint,tail,memory_cursors}'::text[])) EXECUTE FUNCTION stamp_resource()
"""


def upgrade() -> None:
    # Constant default: metadata-only on supported PostgreSQL, no rewrite or historical backfill.
    # IF NOT EXISTS permits restart after the autocommit index phase committed this column.
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS search_vector tsvector NOT NULL DEFAULT ''::tsvector")
    op.execute(GUARD_RUN)
    op.execute("DROP TRIGGER stamp_resource ON runs")
    op.execute(STAMP)
    # Keep writes available during the GIN scan. A failed concurrent build may leave an invalid index.
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('ix_runs_search_vector')")
        )
        if valid is False:
            op.drop_index("ix_runs_search_vector", postgresql_concurrently=True)
        op.create_index(
            "ix_runs_search_vector",
            "runs",
            ["search_vector"],
            unique=False,
            postgresql_using="gin",
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    # Stop new workers before schema rollback; already written format-2 tails require new readers.
    op.execute("DROP TRIGGER stamp_resource ON runs")
    op.execute(PREVIOUS_STAMP)
    op.execute(PREVIOUS_GUARD_RUN)
    op.drop_index("ix_runs_search_vector", table_name="runs", postgresql_using="gin")
    op.drop_column("runs", "search_vector")
