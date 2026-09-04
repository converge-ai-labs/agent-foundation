"""replace multi api models with settings defaults.

Revision ID: 91d29d5362ae
Revises: 023eff74515b
Create Date: 2026-09-04 09:32:30.433738+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "91d29d5362ae"
down_revision: str | Sequence[str] | None = "023eff74515b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM models)")):
        raise RuntimeError(
            "Single-API Models require a fresh development database; export configuration and recreate it explicitly."
        )
    op.add_column("models", sa.Column("model_api", sa.String(length=96), nullable=False))
    op.add_column("models", sa.Column("settings", sa.JSON(), nullable=False))
    op.add_column("models", sa.Column("profile", sa.JSON(), nullable=False))
    op.add_column("models", sa.Column("limits", sa.JSON(), nullable=False))
    op.drop_column("models", "model_apis")


def downgrade() -> None:
    """Reverse an empty development schema without discarding new settings."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM models)")):
        raise RuntimeError("Cannot downgrade populated single-API Models; use forward repair.")
    op.add_column(
        "models", sa.Column("model_apis", postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=False)
    )
    op.drop_column("models", "limits")
    op.drop_column("models", "profile")
    op.drop_column("models", "settings")
    op.drop_column("models", "model_api")
