"""index configured usage models.

Revision ID: 63bab3fe8f7b
Revises: e94e15093b5d
Create Date: 2026-11-11 02:49:14.943074+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "63bab3fe8f7b"
down_revision: str | Sequence[str] | None = "e94e15093b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "usage_model",
        sa.Column("recipe_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("recipe_id", name=op.f("pk_usage_model")),
    )

    # Only seed keys. Captured configurations live outside SQLite and are read
    # by background maintenance after the migration transaction has finished.
    op.execute("""
        INSERT OR IGNORE INTO usage_model (recipe_id)
        SELECT json_extract(record.value, '$.model_id')
          FROM thread_usage, json_each(
            CASE json_extract(payload_json, '$.kind')
              WHEN 'snapshot' THEN json_extract(payload_json, '$.snapshot.records')
              ELSE json_array(json(payload_json)) END
          ) AS record
         WHERE json_extract(record.value, '$.model_id') GLOB 'a13n-harness-ui:model-*'
    """)


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_table("usage_model")
