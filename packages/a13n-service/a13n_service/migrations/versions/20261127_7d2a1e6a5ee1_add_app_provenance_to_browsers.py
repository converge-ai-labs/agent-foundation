"""add app provenance to browsers

Revision ID: 7d2a1e6a5ee1
Revises: 482d62aeefa1
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7d2a1e6a5ee1"
down_revision = "482d62aeefa1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browsers", sa.Column("app_id", sa.String(length=128), nullable=True))
    op.add_column(
        "browsers", sa.Column("app_context", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True)
    )
    # Both new columns are NULL in every existing row. Enforce the check on writes without scanning under the DDL lock.
    op.create_check_constraint(
        op.f("ck_browsers_app_context_owner"),
        "browsers",
        "app_context IS NULL OR app_id IS NOT NULL",
        postgresql_not_valid=True,
    )
    op.execute(
        "CREATE TRIGGER refuse_mutation BEFORE UPDATE ON browsers FOR EACH ROW"
        " WHEN (OLD.app_id IS DISTINCT FROM NEW.app_id OR OLD.app_context IS DISTINCT FROM NEW.app_context)"
        " EXECUTE FUNCTION refuse_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER refuse_mutation ON browsers")
    op.drop_constraint(op.f("ck_browsers_app_context_owner"), "browsers", type_="check")
    op.drop_column("browsers", "app_context")
    op.drop_column("browsers", "app_id")
