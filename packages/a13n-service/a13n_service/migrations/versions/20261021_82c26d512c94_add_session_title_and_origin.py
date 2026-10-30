"""add session title and origin

Revision ID: 82c26d512c94
Revises: 36c442fa1a5e
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "82c26d512c94"
down_revision = "36c442fa1a5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable metadata only: no default, table rewrite, or historical backfill.
    op.add_column("sessions", sa.Column("title", sa.String(length=256), nullable=True))
    op.add_column(
        "sessions", sa.Column("origin", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sessions", "origin")
    op.drop_column("sessions", "title")
