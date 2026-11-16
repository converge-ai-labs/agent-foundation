"""track sealed transcript prefixes.

Revision ID: 2a7cffc5be82
Revises: 172cb05de184
Create Date: 2026-11-14 10:51:39.110988+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a7cffc5be82"
down_revision: str | Sequence[str] | None = "172cb05de184"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("thread_inspection", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sealed_prefix_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("thread_inspection", schema=None) as batch_op:
        batch_op.drop_column("sealed_prefix_digest")
