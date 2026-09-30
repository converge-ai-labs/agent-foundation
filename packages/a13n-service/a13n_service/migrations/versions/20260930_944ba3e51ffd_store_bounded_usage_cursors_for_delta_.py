"""store bounded usage cursors for delta ingestion

Revision ID: 944ba3e51ffd
Revises: 123fec952fe1
"""

import sqlalchemy as sa
from alembic import op

revision = "944ba3e51ffd"
down_revision = "123fec952fe1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Build only the small scope-owner index, without blocking normal writes.
    # Concurrent DDL can survive an interrupted migration; repair an invalid
    # partial build before retrying, and reuse an already valid index.
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('ix_usage_records_scope_owner')")
        )
        if valid is False:
            op.drop_index("ix_usage_records_scope_owner", postgresql_concurrently=True)
        op.create_index(
            "ix_usage_records_scope_owner",
            "usage_records",
            ["run_attempt_id", "harness_run_id"],
            unique=False,
            postgresql_where=sa.text("record->>'kind' IN ('snapshot', 'cursor')"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    # Both old snapshot writers and new cursor writers remain valid during rollout.
    op.execute(
        """
CREATE OR REPLACE FUNCTION guard_usage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'usage cannot be deleted'; END IF;
    IF (to_jsonb(NEW) - '{record,digest}'::text[])
        IS DISTINCT FROM (to_jsonb(OLD) - '{record,digest}'::text[])
    THEN RAISE EXCEPTION 'usage attribution is immutable'; END IF;
    IF NEW.record->>'kind' IS DISTINCT FROM OLD.record->>'kind'
    THEN RAISE EXCEPTION 'usage kind is immutable'; END IF;
    IF OLD.record->>'kind' IN ('snapshot', 'cursor') THEN
        IF (NEW.record->>'sequence')::bigint <= (OLD.record->>'sequence')::bigint
        THEN RAISE EXCEPTION 'usage snapshot sequence must advance'; END IF;
    ELSIF OLD.record->>'kind' != 'model' OR NOT EXISTS (
        SELECT 1 FROM usage_records scope
        WHERE scope.record->>'kind' IN ('snapshot', 'cursor')
          AND scope.run_attempt_id = OLD.run_attempt_id
          AND scope.harness_run_id = OLD.harness_run_id
    ) THEN RAISE EXCEPTION 'legacy facts and provider receipts are immutable'; END IF;
    RETURN NEW;
END $$
"""
    )


def downgrade() -> None:
    op.execute(
        """
CREATE OR REPLACE FUNCTION guard_usage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'usage cannot be deleted'; END IF;
    IF (to_jsonb(NEW) - '{record,digest}'::text[])
        IS DISTINCT FROM (to_jsonb(OLD) - '{record,digest}'::text[])
    THEN RAISE EXCEPTION 'usage attribution is immutable'; END IF;
    IF NEW.record->>'kind' IS DISTINCT FROM OLD.record->>'kind'
    THEN RAISE EXCEPTION 'usage kind is immutable'; END IF;
    IF OLD.record->>'kind' = 'snapshot' THEN
        IF (NEW.record->>'sequence')::bigint <= (OLD.record->>'sequence')::bigint
        THEN RAISE EXCEPTION 'usage snapshot sequence must advance'; END IF;
    ELSIF OLD.record->>'kind' != 'model' OR NOT EXISTS (
        SELECT 1 FROM usage_records scope
        WHERE scope.record->>'kind' = 'snapshot'
          AND scope.run_attempt_id = OLD.run_attempt_id
          AND scope.harness_run_id = OLD.harness_run_id
    ) THEN RAISE EXCEPTION 'legacy facts and provider receipts are immutable'; END IF;
    RETURN NEW;
END $$
"""
    )
    op.drop_index("ix_usage_records_scope_owner", table_name="usage_records")
