"""retain configured routes for chat usage.

Revision ID: 2444030a5191
Revises: 63bab3fe8f7b
Create Date: 2026-11-11 03:50:32.120141+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2444030a5191"
down_revision: str | Sequence[str] | None = "63bab3fe8f7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("usage_model", sa.Column("route", sa.String(length=512), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("usage_model", "route")
