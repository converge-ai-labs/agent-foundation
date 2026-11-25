"""store environment instance recipe overrides

Revision ID: 952161a3d532
Revises: 4965456efc57
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "952161a3d532"
down_revision = "4965456efc57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("recipe_overrides", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("environments", "recipe_overrides")
