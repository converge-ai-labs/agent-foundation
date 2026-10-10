"""reserve environments until first use

Revision ID: 43f34f1a0183
Revises: 7dc8ec393cc1
"""

import sqlalchemy as sa
from alembic import op

revision = "43f34f1a0183"
down_revision = "7dc8ec393cc1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogeneration does not detect changed check expressions or index predicates.
    op.drop_constraint(op.f("ck_environments_status"), "environments", type_="check")
    op.create_check_constraint(
        op.f("ck_environments_status"),
        "environments",
        "status IN ('reserved', 'creating', 'starting', 'ready', 'stopping', 'stopped', 'deleting', 'deleted')",
    )
    op.drop_index("ix_environments_idle", table_name="environments")
    op.create_index(
        "ix_environments_idle",
        "environments",
        ["last_used_at"],
        postgresql_where=sa.text("template_id IS NOT NULL AND status IN ('reserved', 'ready', 'stopped')"),
    )
    op.create_check_constraint(
        op.f("ck_environments_reserved"),
        "environments",
        "status <> 'reserved' OR (handle IS NULL AND provider_identity IS NULL AND failure IS NULL)",
    )


def downgrade() -> None:
    # Refuse rollback while reservations exist; neither allocate nor erase targets during schema rollback.
    op.drop_constraint(op.f("ck_environments_status"), "environments", type_="check")
    op.create_check_constraint(
        op.f("ck_environments_status"),
        "environments",
        "status IN ('creating', 'starting', 'ready', 'stopping', 'stopped', 'deleting', 'deleted')",
    )
    op.drop_index("ix_environments_idle", table_name="environments")
    op.create_index(
        "ix_environments_idle",
        "environments",
        ["last_used_at"],
        postgresql_where=sa.text("template_id IS NOT NULL AND status IN ('ready', 'stopped')"),
    )
    op.drop_constraint(op.f("ck_environments_reserved"), "environments", type_="check")
