"""allow running agents to append environment mounts

Revision ID: 6cd6ff2b17bc
Revises: 7d2a1e6a5ee1
"""

from alembic import op

revision = "6cd6ff2b17bc"
down_revision = "7d2a1e6a5ee1"
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
                IF NEW.environment_mounts IS DISTINCT FROM OLD.environment_mounts THEN
                    IF OLD.status <> 'running' OR NEW.status <> 'running'
                        OR OLD.cancel_requested_at IS NOT NULL OR NEW.cancel_requested_at IS NOT NULL
                        OR jsonb_typeof(NEW.environment_mounts) <> 'array'
                    THEN RAISE EXCEPTION 'only running runs may append environment mounts'; END IF;
                    IF jsonb_array_length(NEW.environment_mounts) < jsonb_array_length(OLD.environment_mounts)
                        OR EXISTS (
                            SELECT 1 FROM jsonb_array_elements(OLD.environment_mounts) WITH ORDINALITY AS m(value, position)
                            WHERE NEW.environment_mounts->(m.position::int - 1) IS DISTINCT FROM m.value
                        ) OR EXISTS (
                            SELECT 1 FROM jsonb_array_elements(NEW.environment_mounts) AS m(value)
                            GROUP BY m.value->>'name' HAVING count(*) > 1 OR m.value->>'name' IS NULL
                        )
                    THEN RAISE EXCEPTION 'run environment mounts are append-only with unique names'; END IF;
                    new_facts.environment_mounts := OLD.environment_mounts;
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


def upgrade() -> None:
    # Replace the trigger function only: no table scan, rewrite or historical backfill.
    op.execute(GUARD_RUN)


def downgrade() -> None:
    # Disable connections and drain new workers before restoring immutable Run selections.
    op.execute(PREVIOUS_GUARD_RUN)
