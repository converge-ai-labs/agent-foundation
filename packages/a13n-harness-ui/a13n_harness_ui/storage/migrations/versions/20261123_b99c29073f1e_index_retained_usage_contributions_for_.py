"""index retained usage contributions for analytics.

Revision ID: b99c29073f1e
Revises: 8b9484d98daa
Create Date: 2026-11-23 11:14:18.109021+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b99c29073f1e"
down_revision: str | Sequence[str] | None = "8b9484d98daa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "usage_fact",
        sa.Column("root_thread_id", sa.String(length=80), nullable=False),
        sa.Column("record_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("recipe_id", sa.String(), nullable=True),
        sa.Column("model_key", sa.String(), nullable=True),
        sa.Column("model_label", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sequence"],
            ["thread_usage.sequence"],
            name=op.f("fk_usage_fact_sequence_thread_usage"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("root_thread_id", "record_id", name=op.f("pk_usage_fact")),
    )
    with op.batch_alter_table("usage_fact", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_usage_fact_sequence"), ["sequence"], unique=False)
        batch_op.create_index(batch_op.f("ix_usage_fact_day"), ["day"], unique=False)
        batch_op.create_index("ix_usage_fact_root_day", ["root_thread_id", "day"], unique=False)

    with op.batch_alter_table("thread_usage", schema=None) as batch_op:
        batch_op.add_column(sa.Column("indexed_through", sa.DateTime(), nullable=True))
        batch_op.create_index(
            "ix_thread_usage_unindexed", ["sequence"], unique=False, sqlite_where=sa.text("indexed_through IS NULL")
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_table("usage_fact")
    with op.batch_alter_table("thread_usage", schema=None) as batch_op:
        batch_op.drop_index("ix_thread_usage_unindexed", sqlite_where=sa.text("indexed_through IS NULL"))
        batch_op.drop_column("indexed_through")
