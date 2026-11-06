"""add file memory guidance presets

Revision ID: fb12402ebb2b
Revises: f6f3e4930b08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "fb12402ebb2b"
down_revision = "f6f3e4930b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories", sa.Column("guidance", postgresql.JSONB(astext_type=sa.Text(), none_as_null=True), nullable=True)
    )
    # Preserve authored rules once while removing the old file-guide representation.
    op.execute("""
        UPDATE memories SET guidance = jsonb_build_object(
            'system_preset', 'general', 'system_override', guide, 'custom', '', 'timezone', 'UTC'
        ), guide = NULL WHERE kind = 'file'
    """)
    op.create_check_constraint(op.f("ck_memories_guidance"), "memories", "(kind = 'file') = (guidance IS NOT NULL)")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_memories_guidance"), "memories", type_="check")
    op.execute("UPDATE memories SET guide = guidance->>'system_override' WHERE kind = 'file'")
    op.drop_column("memories", "guidance")
