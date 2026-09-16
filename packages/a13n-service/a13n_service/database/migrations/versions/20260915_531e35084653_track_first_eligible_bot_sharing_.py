"""track first eligible bot sharing configuration.

Revision ID: 531e35084653
Revises: fa5bed0a37f2
Create Date: 2026-09-15 17:54:42.908129+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "531e35084653"
down_revision: str | Sequence[str] | None = "fa5bed0a37f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # Existing scopes must not become newly eligible solely because of this rollout.
    op.add_column(
        "bot_memory_scopes",
        sa.Column("sharing_initialized", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.alter_column("bot_memory_scopes", "sharing_initialized", server_default=sa.text("false"))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("bot_memory_scopes", "sharing_initialized")
