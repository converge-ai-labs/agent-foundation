"""keep run version across checkpoint commits

Revision ID: 3fe004454022
Revises: c901bbbf2b2c

Replaces the runs version trigger so a change confined to checkpoint, tail and memory cursors keeps the run's
version and updated_at. Replacing the trigger briefly locks runs and scans nothing. Older builds read the version
only as an ETag, so they run unchanged against either trigger. Transactional failure rolls back and retries
safely; downgrade restores the unconditional trigger.
"""

from alembic import op

revision = "3fe004454022"
down_revision = "c901bbbf2b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER stamp_resource ON runs")
    op.execute(
        """
    CREATE TRIGGER stamp_resource BEFORE UPDATE ON runs FOR EACH ROW WHEN ((to_jsonb(OLD) - '{checkpoint,tail,memory_cursors}'::text[]) IS DISTINCT FROM (to_jsonb(NEW) - '{checkpoint,tail,memory_cursors}'::text[])) EXECUTE FUNCTION stamp_resource()
    """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER stamp_resource ON runs")
    op.execute(
        """
    CREATE TRIGGER stamp_resource BEFORE UPDATE ON runs FOR EACH ROW EXECUTE FUNCTION stamp_resource()
    """
    )
