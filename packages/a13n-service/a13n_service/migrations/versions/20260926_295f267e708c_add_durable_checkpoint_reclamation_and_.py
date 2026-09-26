"""add durable checkpoint reclamation and outbox settlement retention

Revision ID: 295f267e708c
Revises: f1935177fc89
"""

import sqlalchemy as sa
from alembic import op

revision = "295f267e708c"
down_revision = "f1935177fc89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_outbox_kind"), "outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_kind"),
        "outbox",
        "kind IN ('webhook', 'child_result', 'email', 'memory_purge', 'checkpoint_cleanup')",
    )
    op.add_column("outbox", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index(
        op.f("ix_outbox_settled"), table_name="outbox", postgresql_where="((status)::text <> 'pending'::text)"
    )
    op.create_index(
        "ix_outbox_settled",
        "outbox",
        ["kind", "status", "settled_at", "id"],
        unique=False,
        postgresql_where=sa.text("status <> 'pending'"),
    )
    op.create_check_constraint(op.f("ck_outbox_settled"), "outbox", "(status <> 'pending') = (settled_at IS NOT NULL)")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_outbox_kind"), "outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_kind"), "outbox", "kind IN ('webhook', 'child_result', 'email', 'memory_purge')"
    )
    op.drop_constraint(op.f("ck_outbox_settled"), "outbox", type_="check")
    op.drop_index("ix_outbox_settled", table_name="outbox", postgresql_where=sa.text("status <> 'pending'"))
    op.create_index(
        op.f("ix_outbox_settled"),
        "outbox",
        ["created_at"],
        unique=False,
        postgresql_where="((status)::text <> 'pending'::text)",
    )
    op.drop_column("outbox", "settled_at")
