"""align service value bounds and asset provenance.

Revision ID: 500f765e612a
Revises: 32fd540534f7
Create Date: 2026-09-08 07:40:08.418320+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "500f765e612a"
down_revision: str | Sequence[str] | None = "32fd540534f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NAME_TABLES = (
    "agents",
    "application_accounts",
    "connector_connections",
    "connector_providers",
    "mcp_connections",
    "model_providers",
    "service_accounts",
    "workspaces",
)


def upgrade() -> None:
    """Widen uniqueness keys without changing their collation or identity."""
    for table in _NAME_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "normalized_name", existing_type=sa.String(128), type_=sa.String(384), existing_nullable=False
            )
    with op.batch_alter_table("assets") as batch:
        # The existing CHECK already excludes filenames beyond 256 characters.
        batch.alter_column("filename", existing_type=sa.String(1024), type_=sa.String(256), existing_nullable=False)


def downgrade() -> None:
    """Refuse overlong PostgreSQL keys rather than truncate unique identities."""
    with op.batch_alter_table("assets") as batch:
        batch.alter_column("filename", existing_type=sa.String(256), type_=sa.String(1024), existing_nullable=False)
    for table in reversed(_NAME_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "normalized_name", existing_type=sa.String(384), type_=sa.String(128), existing_nullable=False
            )
