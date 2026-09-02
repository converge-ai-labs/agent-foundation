"""rename active revision to default revision.

Revision ID: 851696cdd6ac
Revises: 4d180002f87e
Create Date: 2026-09-02 04:16:59.055209+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "851696cdd6ac"
down_revision: str | Sequence[str] | None = "4d180002f87e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.alter_column(
        "agent_presets",
        "active_revision_id",
        new_column_name="default_revision_id",
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.alter_column(
        "agent_presets",
        "default_revision_id",
        new_column_name="active_revision_id",
    )
