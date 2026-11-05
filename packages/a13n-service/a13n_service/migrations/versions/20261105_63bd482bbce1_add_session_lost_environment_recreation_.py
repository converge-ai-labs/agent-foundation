"""add session lost environment recreation setting

Revision ID: 63bd482bbce1
Revises: a3d0f1eb7aa7
"""

import sqlalchemy as sa
from alembic import op

revision = "63bd482bbce1"
down_revision = "a3d0f1eb7aa7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("auto_recreate_lost_environment", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    # Serialize the check with Session edits before dropping their opt-out.
    op.execute("LOCK TABLE sessions IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT FROM sessions WHERE NOT auto_recreate_lost_environment)")):
        raise RuntimeError("Cannot drop auto_recreate_lost_environment while sessions have opted out")
    op.drop_column("sessions", "auto_recreate_lost_environment")
