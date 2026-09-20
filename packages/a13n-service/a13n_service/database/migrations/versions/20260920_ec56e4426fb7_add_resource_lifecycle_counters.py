"""add resource lifecycle counters.

Revision ID: ec56e4426fb7
Revises: 5fd38c1c77ba
Create Date: 2026-09-20 06:26:41.549364+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ec56e4426fb7"
down_revision: str | Sequence[str] | None = "5fd38c1c77ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Initialize resource high watermarks before counter-based writers start.

    Drain older Run/Attempt writers before applying this revision: writers that
    allocate from MAX(events) do not maintain the new counters. The additive
    columns and backfill commit atomically under the migration's DDL locks and
    bounded statement timeout; a failed migration leaves no partial cutover.
    """
    op.add_column("run_attempts", sa.Column("lifecycle_seq", sa.BigInteger(), server_default="0", nullable=False))
    op.create_check_constraint(op.f("ck_run_attempts_lifecycle_seq_nonnegative"), "run_attempts", "lifecycle_seq >= 0")
    op.add_column("runs", sa.Column("lifecycle_seq", sa.BigInteger(), server_default="0", nullable=False))
    op.create_check_constraint(op.f("ck_runs_lifecycle_seq_nonnegative"), "runs", "lifecycle_seq >= 0")
    _terminal_guards(with_counters=True)
    for table, entity_type in (("runs", "run"), ("run_attempts", "run_attempt")):
        # Retained history supplies the exact watermark. When all facts have
        # expired, the resource version is a conservative upper bound, so an
        # old delivery's sequence cannot be reused after this cutover.
        op.execute(
            sa.text(
                f"UPDATE {table} AS resource SET lifecycle_seq = COALESCE("
                "(SELECT MAX(event.resource_seq) FROM lifecycle_events AS event "
                "WHERE event.organization_id = resource.organization_id "
                "AND event.entity_type = :entity_type AND event.entity_id = resource.id), "
                "resource.version)"
            ).bindparams(entity_type=entity_type)
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    _terminal_guards(with_counters=False)
    op.drop_constraint(op.f("ck_runs_lifecycle_seq_nonnegative"), "runs", type_="check")
    op.drop_column("runs", "lifecycle_seq")
    op.drop_constraint(op.f("ck_run_attempts_lifecycle_seq_nonnegative"), "run_attempts", type_="check")
    op.drop_column("run_attempts", "lifecycle_seq")


def _terminal_guards(*, with_counters: bool) -> None:
    counter = " - 'lifecycle_seq'" if with_counters else ""
    attempt_change = (
        "AND (to_jsonb(NEW) - 'lifecycle_seq') IS DISTINCT FROM (to_jsonb(OLD) - 'lifecycle_seq')"
        if with_counters
        else ""
    )
    monotonic_counter = (
        "IF NEW.lifecycle_seq < OLD.lifecycle_seq THEN RAISE EXCEPTION 'lifecycle sequence cannot decrease'; END IF;"
        if with_counters
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION reject_terminal_interaction_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            {monotonic_counter}
            IF TG_TABLE_NAME = 'runs'
               AND OLD.status IN ('waiting', 'completed', 'failed', 'cancelled')
               AND (to_jsonb(NEW) - 'labels' - 'updated_at'{counter})
                   IS DISTINCT FROM (to_jsonb(OLD) - 'labels' - 'updated_at'{counter}) THEN
                RAISE EXCEPTION 'sealed Run rows are immutable';
            END IF;
            IF TG_TABLE_NAME = 'run_attempts'
               AND OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled')
               {attempt_change} THEN
                RAISE EXCEPTION 'terminal RunAttempt rows are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
