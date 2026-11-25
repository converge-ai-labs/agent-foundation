"""index chronological workspace asset listing

Revision ID: 4965456efc57
Revises: 5d3f6246fcd7

Build concurrently to keep asset writes available during the scan. Retry repairs
an interrupted build or reuses the valid index if only the revision stamp was lost.
"""

import sqlalchemy as sa
from alembic import op

revision = "4965456efc57"
down_revision = "5d3f6246fcd7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('ix_assets_workspace_created')")
        )
        if valid is False:
            op.drop_index("ix_assets_workspace_created", postgresql_concurrently=True)
        op.create_index(
            "ix_assets_workspace_created",
            "assets",
            ["workspace_id", "created_at", "id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.drop_index("ix_assets_workspace_created", table_name="assets")
