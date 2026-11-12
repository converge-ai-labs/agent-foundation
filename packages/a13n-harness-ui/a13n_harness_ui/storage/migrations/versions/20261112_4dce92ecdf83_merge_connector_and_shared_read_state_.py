"""merge connector and shared read state histories.

Revision ID: 4dce92ecdf83
Revises: 7daf652f9b5d, 9450d8b1a589
Create Date: 2026-11-12 03:49:52.548568+00:00
"""

from collections.abc import Sequence

revision: str = "4dce92ecdf83"
down_revision: str | Sequence[str] | None = ("7daf652f9b5d", "9450d8b1a589")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
