"""accept typed bot memory document kinds.

Revision ID: ba435c2961da
Revises: 6a336ba34b98
Create Date: 2026-09-20 09:37:30.654940+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ba435c2961da"
down_revision: str | Sequence[str] | None = "6a336ba34b98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Admit current kinds without reclassifying historical data."""
    op.drop_constraint(op.f("ck_bot_memory_documents_kind_valid"), "bot_memory_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_bot_memory_documents_kind_valid"),
        "bot_memory_documents",
        "kind IN ('semantic', 'procedural', 'episodic', 'daily', 'long_term')",
    )


def downgrade() -> None:
    """Reject rollback if current-kind rows exist; never relabel or delete them."""
    op.drop_constraint(op.f("ck_bot_memory_documents_kind_valid"), "bot_memory_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_bot_memory_documents_kind_valid"), "bot_memory_documents", "kind IN ('daily', 'long_term')"
    )
