"""add session purpose.

Revision ID: f2abf114c374
Revises: e0bca206450c
Create Date: 2026-09-18 09:48:02.461044+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2abf114c374"
down_revision: str | Sequence[str] | None = "e0bca206450c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("sessions", sa.Column("purpose", sa.String(length=16), server_default="execution", nullable=False))
    op.create_check_constraint(
        op.f("ck_sessions_session_purpose_valid"), "sessions", "purpose IN ('execution', 'debug')"
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_constraint(op.f("ck_sessions_session_purpose_valid"), "sessions", type_="check")
    op.drop_column("sessions", "purpose")
