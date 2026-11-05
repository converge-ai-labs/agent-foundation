"""record idle environment retirement

Revision ID: 41e808ec703b
Revises: 3fd7115ea45e
"""

import sqlalchemy as sa
from alembic import op

revision = "41e808ec703b"
down_revision = "3fd7115ea45e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "environments", sa.Column("retired_for_idle", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )


def downgrade() -> None:
    op.execute("LOCK TABLE environments IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT FROM environments WHERE retired_for_idle AND replaces_environment_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("Cannot discard idle retirement of a replacement environment")
    op.drop_column("environments", "retired_for_idle")
