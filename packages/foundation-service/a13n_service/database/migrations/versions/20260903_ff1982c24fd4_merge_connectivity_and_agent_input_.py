"""merge connectivity and agent input histories.

Revision ID: ff1982c24fd4
Revises: a5dae1fc588f, ae2c76a48f97
Create Date: 2026-09-03 09:33:05.760761+00:00
"""

from collections.abc import Sequence

revision: str = "ff1982c24fd4"
down_revision: str | Sequence[str] | None = ("a5dae1fc588f", "ae2c76a48f97")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
