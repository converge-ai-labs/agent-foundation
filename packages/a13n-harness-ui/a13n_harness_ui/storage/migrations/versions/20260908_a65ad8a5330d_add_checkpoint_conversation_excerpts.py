"""add checkpoint conversation excerpts.

Revision ID: a65ad8a5330d
Revises: c8a41c768449
Create Date: 2026-09-08 12:20:26.120632+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a65ad8a5330d"
down_revision: str | Sequence[str] | None = "c8a41c768449"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.add_column(sa.Column("search_text", sa.Text(), server_default=sa.text("('')"), nullable=False))
        batch_op.add_column(
            sa.Column("first_input", sa.String(length=512), server_default=sa.text("('')"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("latest_input", sa.String(length=2048), server_default=sa.text("('')"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("latest_reply", sa.String(length=2048), server_default=sa.text("('')"), nullable=False)
        )
        batch_op.add_column(sa.Column("reply_kind", sa.String(length=16), server_default="none", nullable=False))
        batch_op.add_column(sa.Column("activity_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_thread_activity_at"), ["activity_at"], unique=False)

    # Preserve name/ID search for existing local sessions without reading checkpoints.
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT thread_id, title FROM thread"))
    for batch in rows.partitions(256):
        connection.execute(
            sa.text("UPDATE thread SET search_text = :search_text WHERE thread_id = :thread_id"),
            [
                {"thread_id": row.thread_id, "search_text": f"{row.thread_id}\n{row.title or ''}".casefold()}
                for row in batch
            ],
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_thread_activity_at"))
        batch_op.drop_column("activity_at")
        batch_op.drop_column("reply_kind")
        batch_op.drop_column("latest_reply")
        batch_op.drop_column("latest_input")
        batch_op.drop_column("first_input")
        batch_op.drop_column("search_text")
