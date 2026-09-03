"""add queued submission failure evidence.

Revision ID: 93f7e255236d
Revises: 18f466b3ccf1
Create Date: 2026-09-03 10:04:54.279651+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93f7e255236d"
down_revision: str | Sequence[str] | None = "18f466b3ccf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("thread_queued_submissions") as batch:
        batch.add_column(sa.Column("failure_json", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint(op.f("ck_thread_queued_submissions_lifecycle_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_thread_queued_submissions_lifecycle_valid"),
            "(position IS NOT NULL AND position >= 1 "
            "AND consumed_run_id IS NULL AND consumed_at IS NULL "
            "AND failure_json IS NULL AND failed_at IS NULL) OR "
            "(position IS NULL AND consumed_run_id IS NOT NULL AND consumed_at IS NOT NULL "
            "AND failure_json IS NULL AND failed_at IS NULL) OR "
            "(position IS NULL AND consumed_run_id IS NULL AND consumed_at IS NULL "
            "AND failure_json IS NOT NULL AND failed_at IS NOT NULL)",
        )
    op.drop_index("ix_thread_queued_submissions_live", table_name="thread_queued_submissions")
    op.drop_index("uq_thread_queued_submissions_position", table_name="thread_queued_submissions")
    op.create_index(
        "ix_thread_queued_submissions_live",
        "thread_queued_submissions",
        ["tenant_id", "thread_id", "position", "id"],
        unique=False,
        postgresql_where=sa.text("position IS NOT NULL"),
    )
    op.create_index(
        "uq_thread_queued_submissions_position",
        "thread_queued_submissions",
        ["tenant_id", "thread_id", "position"],
        unique=True,
        postgresql_where=sa.text("position IS NOT NULL"),
    )
    op.create_index(
        "ix_thread_queued_submissions_failed",
        "thread_queued_submissions",
        ["tenant_id", "thread_id", "failed_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    failed_count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM thread_queued_submissions WHERE failed_at IS NOT NULL"))
        .scalar_one()
    )
    if failed_count:
        raise RuntimeError("cannot downgrade while failed queued submissions are retained")
    op.drop_index("ix_thread_queued_submissions_failed", table_name="thread_queued_submissions")
    op.drop_index("ix_thread_queued_submissions_live", table_name="thread_queued_submissions")
    op.drop_index("uq_thread_queued_submissions_position", table_name="thread_queued_submissions")
    op.create_index(
        "ix_thread_queued_submissions_live",
        "thread_queued_submissions",
        ["tenant_id", "thread_id", "position", "id"],
        unique=False,
        postgresql_where=sa.text("consumed_run_id IS NULL"),
    )
    op.create_index(
        "uq_thread_queued_submissions_position",
        "thread_queued_submissions",
        ["tenant_id", "thread_id", "position"],
        unique=True,
        postgresql_where=sa.text("consumed_run_id IS NULL"),
    )
    with op.batch_alter_table("thread_queued_submissions") as batch:
        batch.drop_constraint(op.f("ck_thread_queued_submissions_lifecycle_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_thread_queued_submissions_lifecycle_valid"),
            "(position IS NOT NULL AND position >= 1 "
            "AND consumed_run_id IS NULL AND consumed_at IS NULL) OR "
            "(position IS NULL AND consumed_run_id IS NOT NULL AND consumed_at IS NOT NULL)",
        )
        batch.drop_column("failed_at")
        batch.drop_column("failure_json")
