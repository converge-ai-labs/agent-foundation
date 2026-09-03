"""track current connector catalog.

Revision ID: 936c2b380ea3
Revises: ff1982c24fd4
Create Date: 2026-09-03 09:33:15.903612+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "936c2b380ea3"
down_revision: str | Sequence[str] | None = "ff1982c24fd4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("connector_connections") as batch_op:
        batch_op.add_column(sa.Column("current_catalog_digest", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            op.f("ck_connector_connections_current_catalog_digest_valid"),
            "current_catalog_digest IS NULL OR length(current_catalog_digest) = 64",
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("connector_connections") as batch_op:
        batch_op.drop_constraint(op.f("ck_connector_connections_current_catalog_digest_valid"), type_="check")
        batch_op.drop_column("current_catalog_digest")
