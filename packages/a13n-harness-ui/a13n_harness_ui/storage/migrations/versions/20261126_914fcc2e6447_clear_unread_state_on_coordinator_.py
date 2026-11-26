"""clear unread state on coordinator workers.

Revision ID: 914fcc2e6447
Revises: 25cea71e51f9
Create Date: 2026-11-26 08:43:24.432304+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "914fcc2e6447"
down_revision: str | Sequence[str] | None = "25cea71e51f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.execute(
        sa.text(
            "UPDATE thread "
            "SET unread = 0, read_state_version = read_state_version + 1 "
            "WHERE unread = 1 "
            "AND thread_id IN (SELECT worker_thread_id FROM coordinator_worker)"
        )
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    pass
