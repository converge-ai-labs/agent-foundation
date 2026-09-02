"""enforce terminal interaction immutability.

Revision ID: 93062df87c6a
Revises: 56a57c531d40
Create Date: 2026-09-02 17:01:41.264940+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "93062df87c6a"
down_revision: str | Sequence[str] | None = "56a57c531d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_terminal_interaction_update()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_TABLE_NAME = 'runs'
                   AND OLD.status IN ('waiting', 'completed', 'failed', 'cancelled') THEN
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
        return
    op.execute(
        """
        CREATE TRIGGER reject_sealed_run_update
        BEFORE UPDATE ON runs
        WHEN OLD.status IN ('waiting', 'completed', 'failed', 'cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'sealed Run rows are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_terminal_run_attempt_update
        BEFORE UPDATE ON run_attempts
        WHEN OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled')
        BEGIN
            SELECT RAISE(ABORT, 'terminal RunAttempt rows are immutable');
        END
        """
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER reject_terminal_run_attempt_update ON run_attempts")
        op.execute("DROP TRIGGER reject_sealed_run_update ON runs")
        op.execute("DROP FUNCTION reject_terminal_interaction_update()")
        return
    op.execute("DROP TRIGGER reject_terminal_run_attempt_update")
    op.execute("DROP TRIGGER reject_sealed_run_update")
