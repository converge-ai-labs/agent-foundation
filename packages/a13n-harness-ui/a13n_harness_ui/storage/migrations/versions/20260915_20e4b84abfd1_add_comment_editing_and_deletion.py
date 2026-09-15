"""add comment editing and deletion.

Revision ID: 20e4b84abfd1
Revises: 4b71199c8ee5
Create Date: 2026-09-15 09:33:51.567967+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20e4b84abfd1"
down_revision: str | Sequence[str] | None = "4b71199c8ee5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "output_comment_tombstone",
        sa.Column("comment_id", sa.String(length=80), nullable=False),
        sa.Column("root_thread_id", sa.String(length=80), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["root_thread_id"],
            ["thread.thread_id"],
            name=op.f("fk_output_comment_tombstone_root_thread_id_thread"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("comment_id", name=op.f("pk_output_comment_tombstone")),
    )
    with op.batch_alter_table("output_comment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    # Older writers omit lifecycle columns. They must not resurrect a deleted identity.
    op.execute("""
        CREATE TRIGGER output_comment_no_resurrection BEFORE INSERT ON output_comment
        WHEN EXISTS (SELECT 1 FROM output_comment_tombstone WHERE comment_id = NEW.comment_id)
        BEGIN SELECT RAISE(ABORT, 'comment_deleted_conflict'); END
    """)


def downgrade() -> None:
    """Refuse to lose edit versions or deleted-identity protection."""
    changed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM output_comment WHERE version > 1 "
                "UNION ALL SELECT 1 FROM output_comment_tombstone LIMIT 1"
            )
        )
        .first()
    )
    if changed is not None:
        raise RuntimeError("Cannot downgrade while edited or deleted output comments exist.")
    op.execute("DROP TRIGGER output_comment_no_resurrection")
    with op.batch_alter_table("output_comment", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("version")

    op.drop_table("output_comment_tombstone")
