"""move inbox accounting onto threads.

Revision ID: 56b4ee866468
Revises: ec86f1da8c98
Create Date: 2026-09-12 02:00:18.455135+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "56b4ee866468"
down_revision: str | Sequence[str] | None = "ec86f1da8c98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    ("next_delivery_sequence", "1", "next_delivery_sequence >= 1", "positive"),
    ("pending_count", "0", "pending_count >= 0", "non_negative"),
    ("pending_bytes", "0", "pending_bytes >= 0", "non_negative"),
)


def upgrade() -> None:
    """Move inbox accounting onto threads and drop its old owner."""
    for name, default, condition, suffix in _COLUMNS:
        op.add_column("threads", sa.Column(name, sa.BigInteger(), server_default=default, nullable=False))
        op.create_check_constraint(op.f(f"ck_threads_{name}_{suffix}"), "threads", condition)
    op.drop_table("thread_inbox_counters")


def downgrade() -> None:
    """Restore the old owner before removing the thread columns."""
    op.create_table(
        "thread_inbox_counters",
        sa.Column("thread_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("next_delivery_sequence", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("pending_count", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("pending_bytes", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "next_delivery_sequence >= 1", name=op.f("ck_thread_inbox_counters_next_delivery_sequence_positive")
        ),
        sa.CheckConstraint("pending_bytes >= 0", name=op.f("ck_thread_inbox_counters_pending_bytes_non_negative")),
        sa.CheckConstraint("pending_count >= 0", name=op.f("ck_thread_inbox_counters_pending_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_thread_inbox_counters_organization_id_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_thread_inbox_counters")),
        sa.UniqueConstraint("organization_id", "thread_id", name=op.f("uq_thread_inbox_counters_scope")),
    )
    for name, _, _, suffix in reversed(_COLUMNS):
        op.drop_constraint(op.f(f"ck_threads_{name}_{suffix}"), "threads", type_="check")
        op.drop_column("threads", name)
