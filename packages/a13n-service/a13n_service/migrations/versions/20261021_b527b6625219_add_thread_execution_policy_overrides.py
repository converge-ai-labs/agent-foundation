"""add thread execution policy overrides

Revision ID: b527b6625219
Revises: 74bf2ff03e86
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b527b6625219"
down_revision = "74bf2ff03e86"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("execution", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("threads", "execution")
