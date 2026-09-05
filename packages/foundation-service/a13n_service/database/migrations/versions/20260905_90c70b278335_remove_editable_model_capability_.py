"""remove editable model capability metadata.

Revision ID: 90c70b278335
Revises: 0d5f1ae47708
Create Date: 2026-09-05 03:52:13.693494+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "90c70b278335"
down_revision: str | Sequence[str] | None = "0d5f1ae47708"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Discard author-edited capability claims; actual Model settings remain intact."""
    op.drop_column("models", "profile")
    op.drop_column("models", "limits")


def downgrade() -> None:
    """Restore the old columns as unknown metadata, without reconstructing deleted claims."""
    with op.batch_alter_table("models") as batch:
        batch.add_column(sa.Column("limits", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table("models") as batch:
        batch.alter_column("limits", server_default=None, existing_type=sa.JSON())
        batch.alter_column("profile", server_default=None, existing_type=sa.JSON())
