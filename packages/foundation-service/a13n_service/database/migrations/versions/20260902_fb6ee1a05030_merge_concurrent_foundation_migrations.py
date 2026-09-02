"""merge concurrent foundation migrations.

Revision ID: fb6ee1a05030
Revises: 19d36fdbbea8, 851696cdd6ac
Create Date: 2026-09-02 05:53:03.028662+00:00
"""

from collections.abc import Sequence

revision: str = "fb6ee1a05030"
down_revision: str | Sequence[str] | None = ("19d36fdbbea8", "851696cdd6ac")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
