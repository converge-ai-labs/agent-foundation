"""add instance reclamation and debug holds

Revision ID: a3d0f1eb7aa7
Revises: e89c6050e1ba
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3d0f1eb7aa7"
down_revision = "e89c6050e1ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable additions preserve the live-template policy for existing instances.
    op.add_column(
        "environments",
        sa.Column("reclamation", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    )
    op.add_column("environments", sa.Column("preparation_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("environments", sa.Column("preparation_failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("environments", sa.Column("debug_hold_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("environments", "debug_hold_until")
    op.drop_column("environments", "preparation_failed_at")
    op.drop_column("environments", "preparation_deadline")
    op.drop_column("environments", "reclamation")
