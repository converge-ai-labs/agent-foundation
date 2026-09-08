"""index multiple resources per source.

Revision ID: ecdbb45e3c57
Revises: 11422c5bac45
Create Date: 2026-09-08 05:24:59.067344+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ecdbb45e3c57"
down_revision: str | Sequence[str] | None = "11422c5bac45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # Autogenerate detects the redundant unique constraint, but not the primary-key change.
    # SQLite rebuilds this derived index table and copies all existing rows.
    with op.batch_alter_table("resource_index") as batch_op:
        batch_op.drop_constraint("resource_identity", type_="unique")
        batch_op.drop_constraint("pk_resource_index", type_="primary")
        batch_op.create_primary_key("pk_resource_index", ["generation_digest", "resource_kind", "resource_id"])


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM resource_index GROUP BY generation_digest, relative_path HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicates is not None:
        raise RuntimeError(
            "Cannot downgrade a resource index containing multi-server sources; retain the current schema."
        )
    with op.batch_alter_table("resource_index") as batch_op:
        batch_op.drop_constraint("pk_resource_index", type_="primary")
        batch_op.create_primary_key("pk_resource_index", ["generation_digest", "relative_path"])
        batch_op.create_unique_constraint("resource_identity", ["generation_digest", "resource_kind", "resource_id"])
