"""merge connector groups and MCP OAuth.

Revision ID: be2873ae217e
Revises: 60710c8b48e4, 5304b6a9760a
Create Date: 2026-11-12 11:23:44.224297+00:00
"""

from collections.abc import Sequence

revision: str = "be2873ae217e"
down_revision: str | Sequence[str] | None = ("60710c8b48e4", "5304b6a9760a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
