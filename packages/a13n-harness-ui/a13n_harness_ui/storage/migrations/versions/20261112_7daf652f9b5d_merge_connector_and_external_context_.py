"""merge connector and external context histories.

Revision ID: 7daf652f9b5d
Revises: ab7f852de318, f196302d868f
Create Date: 2026-11-12 03:03:14.414121+00:00
"""

from collections.abc import Sequence

revision: str = "7daf652f9b5d"
down_revision: str | Sequence[str] | None = ("ab7f852de318", "f196302d868f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
