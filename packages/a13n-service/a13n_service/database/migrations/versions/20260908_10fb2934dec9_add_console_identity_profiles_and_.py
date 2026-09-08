"""add console identity profiles and recovery.

Revision ID: 10fb2934dec9
Revises: 32fd540534f7
Create Date: 2026-09-08 14:33:24.157609+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "10fb2934dec9"
down_revision: str | Sequence[str] | None = "32fd540534f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "email_change_tokens",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("user_id", sa.String(length=72), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_email", sa.String(length=320), nullable=False),
        sa.Column("new_email", sa.String(length=320), nullable=False),
        sa.Column("new_normalized_email", sa.String(length=320), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_change_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_change_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_change_tokens_token_hash")),
    )
    op.create_index(op.f("ix_email_change_tokens_user_id"), "email_change_tokens", ["user_id"], unique=False)
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("user_id", sa.String(length=72), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_password_reset_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
    )
    op.create_index(op.f("ix_password_reset_tokens_user_id"), "password_reset_tokens", ["user_id"], unique=False)
    op.add_column("organizations", sa.Column("image_id", sa.String(length=72), nullable=True))
    op.add_column("users", sa.Column("image_id", sa.String(length=72), nullable=True))
    op.add_column("workspaces", sa.Column("image_id", sa.String(length=72), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("workspaces", "image_id")
    op.drop_column("users", "image_id")
    op.drop_column("organizations", "image_id")
    op.drop_index(op.f("ix_password_reset_tokens_user_id"), table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index(op.f("ix_email_change_tokens_user_id"), table_name="email_change_tokens")
    op.drop_table("email_change_tokens")
