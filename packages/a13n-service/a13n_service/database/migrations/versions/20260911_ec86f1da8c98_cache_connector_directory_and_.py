"""cache connector directory and coordinate shared setup.

Revision ID: ec86f1da8c98
Revises: 2999349e6c69
Create Date: 2026-09-11 07:55:44.038060+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ec86f1da8c98"
down_revision: str | Sequence[str] | None = "2999349e6c69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # A constant database default also initializes retained providers. Keep it:
    # removing it by rebuilding the SQLite parent could cascade-delete children.
    op.add_column(
        "connector_providers", sa.Column("setup_claims_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
    )
    op.add_column("connector_providers", sa.Column("directory_json", sa.JSON(), nullable=True))
    op.add_column("connector_providers", sa.Column("directory_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("connector_providers", "directory_updated_at")
    op.drop_column("connector_providers", "directory_json")
    op.drop_column("connector_providers", "setup_claims_json")
