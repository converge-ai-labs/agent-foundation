"""support GitHub bot reply observations.

Revision ID: e9d8ec98fb26
Revises: 834b06f14947
Create Date: 2026-09-17 04:50:56.882647+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9d8ec98fb26"
down_revision: str | Sequence[str] | None = "834b06f14947"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # Autogenerate does not compare the expression of an existing named check.
    # All retained rows satisfy this additive provider expansion.
    op.drop_constraint(op.f("ck_bot_replies_provider_valid"), "bot_replies", type_="check")
    op.create_check_constraint(
        op.f("ck_bot_replies_provider_valid"), "bot_replies", "provider_key IN ('slack', 'lark', 'github')"
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    # Downgrade intentionally fails while GitHub evidence exists; prefer forward repair.
    op.drop_constraint(op.f("ck_bot_replies_provider_valid"), "bot_replies", type_="check")
    op.create_check_constraint(
        op.f("ck_bot_replies_provider_valid"), "bot_replies", "provider_key IN ('slack', 'lark')"
    )
