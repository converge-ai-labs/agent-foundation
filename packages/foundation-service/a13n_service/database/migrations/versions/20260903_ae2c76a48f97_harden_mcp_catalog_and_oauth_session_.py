"""harden MCP catalog and OAuth session state.

Revision ID: ae2c76a48f97
Revises: cfbd46a59851
Create Date: 2026-09-03 08:49:33.875434+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ae2c76a48f97"
down_revision: str | Sequence[str] | None = "cfbd46a59851"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.add_column(sa.Column("current_catalog_digest", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            op.f("ck_mcp_connections_current_catalog_digest_valid"),
            "current_catalog_digest IS NULL OR length(current_catalog_digest) = 64",
        )
    with op.batch_alter_table("mcp_oauth_sessions") as batch_op:
        batch_op.alter_column("client_id", existing_type=sa.VARCHAR(length=2048), nullable=True)


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("mcp_oauth_sessions") as batch_op:
        batch_op.alter_column("client_id", existing_type=sa.VARCHAR(length=2048), nullable=False)
    with op.batch_alter_table("mcp_connections") as batch_op:
        batch_op.drop_constraint(op.f("ck_mcp_connections_current_catalog_digest_valid"), type_="check")
        batch_op.drop_column("current_catalog_digest")
