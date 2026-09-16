"""add thread navigation touch time.

Revision ID: 122039abf689
Revises: 20e4b84abfd1
Create Date: 2026-09-16 10:50:07.963935+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "122039abf689"
down_revision: str | Sequence[str] | None = "20e4b84abfd1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("thread", sa.Column("touched_at", sa.DateTime(), nullable=True))
    # Preserve the existing order once; older writers may still omit this column.
    op.execute(sa.text("UPDATE thread SET touched_at = updated_at"))
    op.create_index("ix_thread_touched_at", "thread", ["touched_at"], unique=False)


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_index("ix_thread_touched_at", table_name="thread")
    # Native DROP COLUMN avoids rebuilding the Thread table and its incoming FKs.
    op.drop_column("thread", "touched_at")
