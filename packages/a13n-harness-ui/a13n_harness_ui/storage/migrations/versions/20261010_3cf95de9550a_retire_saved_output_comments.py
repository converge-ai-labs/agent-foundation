"""retire saved output comments.

Revision ID: 3cf95de9550a
Revises: 4bd3d3ab1b82
Create Date: 2026-10-10 08:57:50.978804+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3cf95de9550a"
down_revision: str | Sequence[str] | None = "4bd3d3ab1b82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.drop_table("output_comment_tombstone")
    with op.batch_alter_table("output_comment", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_output_comment_target_order"))
        batch_op.drop_index(batch_op.f("ix_output_comment_thread_order"))

    op.drop_table("output_comment")


def downgrade() -> None:
    """Removed comment records cannot be reconstructed by a schema downgrade."""
    raise RuntimeError("Comment retirement is irreversible; restore a pre-upgrade backup to roll back.")
