"""retain display content references

Revision ID: 7dc8ec393cc1
Revises: f6cbc8d1d262
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7dc8ec393cc1"
down_revision = "f6cbc8d1d262"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An additive constant default keeps existing immutable page rows readable without a backfill.
    op.add_column(
        "run_item_pages",
        sa.Column("refs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    # Application rollback must retain this column while any display references external values.
    op.drop_column("run_item_pages", "refs")
