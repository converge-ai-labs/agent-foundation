"""merge inbox items and usage contribution index.

Revision ID: d41c6701eaa7
Revises: 71473c887b8f, b99c29073f1e
Create Date: 2026-11-23 12:21:25.014180+00:00
"""

from collections.abc import Sequence

revision: str = "d41c6701eaa7"
down_revision: str | Sequence[str] | None = ("71473c887b8f", "b99c29073f1e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
