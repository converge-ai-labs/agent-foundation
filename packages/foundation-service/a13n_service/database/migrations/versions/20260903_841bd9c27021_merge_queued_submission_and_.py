"""merge queued submission and connectivity histories.

Revision ID: 841bd9c27021
Revises: 93f7e255236d, 8839abeb1e8a
Create Date: 2026-09-03 12:46:55.339659+00:00
"""

from collections.abc import Sequence

revision: str = "841bd9c27021"
down_revision: str | Sequence[str] | None = ("93f7e255236d", "8839abeb1e8a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    pass


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
