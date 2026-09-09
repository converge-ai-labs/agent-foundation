"""bind connector setup to browser authorization.

Revision ID: 7b6b3373420a
Revises: 10fb2934dec9
Create Date: 2026-09-09 06:55:20.497296+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b6b3373420a"
down_revision: str | Sequence[str] | None = "10fb2934dec9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # Nullable expansion avoids a backfill and keeps the old application schema readable.
    # Old in-flight Composio attempts are invalidated by the new reconciler, not converted.
    op.add_column("connector_setup_attempts", sa.Column("browser_binding_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("connector_setup_attempts", "browser_binding_digest")
