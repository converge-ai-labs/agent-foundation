"""add agent preset connectivity selections.

Revision ID: 36291770fba1
Revises: fb6ee1a05030
Create Date: 2026-09-02 10:02:51.895339+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "36291770fba1"
down_revision: str | Sequence[str] | None = "fb6ee1a05030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("agent_preset_revisions") as batch_op:
        batch_op.add_column(sa.Column("connector_tools", sa.JSON(), server_default=sa.text("'[]'"), nullable=False))
        batch_op.add_column(sa.Column("mcp_tools", sa.JSON(), server_default=sa.text("'[]'"), nullable=False))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("agent_preset_revisions") as batch_op:
        batch_op.drop_column("mcp_tools")
        batch_op.drop_column("connector_tools")
