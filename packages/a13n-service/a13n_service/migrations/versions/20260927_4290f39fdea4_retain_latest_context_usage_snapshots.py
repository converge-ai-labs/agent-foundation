"""retain latest context usage snapshots

Revision ID: 4290f39fdea4
Revises: 295f267e708c
"""

from alembic import op

revision = "4290f39fdea4"
down_revision = "295f267e708c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No data rewrite: existing append-only facts retain their payload and attribution.
    op.execute("DROP TRIGGER refuse_mutation ON usage_records")
    op.execute("""
CREATE FUNCTION guard_usage() RETURNS trigger LANGUAGE plpgsql AS $$
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
""")
    op.execute(
        "CREATE TRIGGER guard_usage BEFORE UPDATE OR DELETE ON usage_records "
        "FOR EACH ROW EXECUTE FUNCTION guard_usage()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_usage ON usage_records")
    op.execute("DROP FUNCTION guard_usage()")
    op.execute(
        "CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE ON usage_records "
        "FOR EACH ROW EXECUTE FUNCTION refuse_mutation()"
    )
