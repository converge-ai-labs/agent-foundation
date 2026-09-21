"""store queue consumption request keys on runs.

Revision ID: e2290622380e
Revises: 325eebd6d934
Create Date: 2026-09-21 07:02:43.303304+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2290622380e"
down_revision: str | Sequence[str] | None = "325eebd6d934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Transfer retained keys before removing the duplicate queue field.

    Deploy with old Control writers stopped: they still address consumption_key.
    PostgreSQL holds the DDL locks and restores the trigger on transaction rollback.
    """
    op.execute("ALTER TABLE runs DISABLE TRIGGER reject_sealed_run_update")
    op.execute(
        """
        UPDATE runs AS run
        SET request_key = queued.consumption_key
        FROM thread_queued_submissions AS queued
        WHERE queued.consumed_run_id = run.id
          AND queued.organization_id = run.organization_id
          AND queued.thread_id = run.thread_id
          AND queued.consumption_key IS NOT NULL
          AND run.request_key IS NULL
        """
    )
    conflicting = op.get_bind().scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM thread_queued_submissions AS queued
                LEFT JOIN runs AS run ON run.id = queued.consumed_run_id
                WHERE queued.consumption_key IS NOT NULL
                  AND run.request_key IS DISTINCT FROM queued.consumption_key
            )
            """
        )
    )
    if conflicting:
        raise RuntimeError("Queue consumption keys conflict with retained Run keys; repair before migrating")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE runs ENABLE TRIGGER reject_sealed_run_update")
    op.drop_constraint(
        op.f("uq_thread_queued_submissions_consumption_key"), "thread_queued_submissions", type_="unique"
    )
    op.drop_column("thread_queued_submissions", "consumption_key")


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.add_column(
        "thread_queued_submissions",
        sa.Column("consumption_key", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    )
    op.create_unique_constraint(
        op.f("uq_thread_queued_submissions_consumption_key"),
        "thread_queued_submissions",
        ["consumption_key"],
        postgresql_nulls_not_distinct=False,
    )
    op.execute(
        """
        UPDATE thread_queued_submissions AS queued
        SET consumption_key = run.request_key
        FROM runs AS run
        WHERE queued.consumed_run_id = run.id
          AND queued.organization_id = run.organization_id
          AND queued.thread_id = run.thread_id
        """
    )
