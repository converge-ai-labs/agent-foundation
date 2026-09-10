"""add agent avatar.

Revision ID: 2999349e6c69
Revises: c9632d7e8521
Create Date: 2026-09-10 07:07:18.943530+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2999349e6c69"
down_revision: str | Sequence[str] | None = "c9632d7e8521"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("agents", sa.Column("image_id", sa.String(length=72), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("agents", "image_id")
