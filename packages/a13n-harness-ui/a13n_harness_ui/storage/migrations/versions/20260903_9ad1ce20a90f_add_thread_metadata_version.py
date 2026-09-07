"""add thread metadata version.

Revision ID: 9ad1ce20a90f
Revises: f293cefc6ea1
Create Date: 2026-09-03 04:12:56.088101+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9ad1ce20a90f"
down_revision: str | Sequence[str] | None = "f293cefc6ea1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metadata_version", sa.Integer(), nullable=True))

    op.execute(sa.text("UPDATE thread SET metadata_version = 1 WHERE metadata_version IS NULL"))

    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.alter_column(
            "metadata_version",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.drop_column("metadata_version")
