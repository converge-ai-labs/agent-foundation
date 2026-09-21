"""track bounded display recovery scheduling.

Revision ID: 7a06c5f249d7
Revises: b8d5f73032c9
Create Date: 2026-09-20 14:20:03.473584+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a06c5f249d7"
down_revision: str | Sequence[str] | None = "b8d5f73032c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("runs", sa.Column("display_settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("display_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_runs_display_active", "runs", ["id"], unique=False, postgresql_where=sa.text("sealed_at IS NULL")
    )
    op.create_index(
        "ix_runs_display_unsettled",
        "runs",
        ["sealed_at", "id"],
        unique=False,
        postgresql_where=sa.text("sealed_at IS NOT NULL AND display_settled_at IS NULL"),
    )
    op.create_check_constraint(
        op.f("ck_runs_display_settled_after_seal"), "runs", "display_settled_at IS NULL OR sealed_at IS NOT NULL"
    )

    _terminal_guards(with_display=True)


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    _terminal_guards(with_display=False)
    op.drop_constraint(op.f("ck_runs_display_settled_after_seal"), "runs", type_="check")
    op.drop_index(
        "ix_runs_display_unsettled",
        table_name="runs",
        postgresql_where=sa.text("sealed_at IS NOT NULL AND display_settled_at IS NULL"),
    )
    op.drop_index("ix_runs_display_active", table_name="runs", postgresql_where=sa.text("sealed_at IS NULL"))
    op.drop_column("runs", "display_next_attempt_at")
    op.drop_column("runs", "display_settled_at")


def _terminal_guards(*, with_display: bool) -> None:
    display = " - 'display_settled_at' - 'display_next_attempt_at'" if with_display else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION reject_terminal_interaction_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.lifecycle_seq < OLD.lifecycle_seq THEN RAISE EXCEPTION 'lifecycle sequence cannot decrease'; END IF;
            IF TG_TABLE_NAME = 'runs'
               AND OLD.status IN ('waiting', 'completed', 'failed', 'cancelled')
               AND (to_jsonb(NEW) - 'labels' - 'updated_at' - 'lifecycle_seq'{display})
                   IS DISTINCT FROM (to_jsonb(OLD) - 'labels' - 'updated_at' - 'lifecycle_seq'{display}) THEN
                RAISE EXCEPTION 'sealed Run rows are immutable';
            END IF;
            IF TG_TABLE_NAME = 'run_attempts'
               AND OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled')
               AND (to_jsonb(NEW) - 'lifecycle_seq') IS DISTINCT FROM (to_jsonb(OLD) - 'lifecycle_seq') THEN
                RAISE EXCEPTION 'terminal RunAttempt rows are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
