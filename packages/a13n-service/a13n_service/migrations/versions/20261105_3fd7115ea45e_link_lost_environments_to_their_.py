"""link lost environments to their replacements

Revision ID: 3fd7115ea45e
Revises: 63bd482bbce1
"""

import sqlalchemy as sa
from alembic import op

revision = "3fd7115ea45e"
down_revision = "63bd482bbce1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These two changes commit together before the online index build. Existing rows have no predecessor.
    if "replaces_environment_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("environments")}:
        op.add_column("environments", sa.Column("replaces_environment_id", sa.String(length=72), nullable=True))
        op.create_foreign_key(
            "fk_environments_predecessor",
            "environments",
            "environments",
            ["workspace_id", "replaces_environment_id"],
            ["workspace_id", "id"],
            postgresql_not_valid=True,
        )
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('uq_environments_replaces')")
        )
        if valid is False:
            op.drop_index("uq_environments_replaces", postgresql_concurrently=True)
        op.create_index(
            "uq_environments_replaces",
            "environments",
            ["replaces_environment_id"],
            unique=True,
            postgresql_where=sa.text("replaces_environment_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    op.execute("ALTER TABLE environments VALIDATE CONSTRAINT fk_environments_predecessor")


def downgrade() -> None:
    op.execute("LOCK TABLE environments IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT FROM environments WHERE replaces_environment_id IS NOT NULL)")
    ):
        raise RuntimeError("Cannot drop environment replacement links after recovery")
    op.drop_constraint("fk_environments_predecessor", "environments", type_="foreignkey")
    op.drop_index(
        "uq_environments_replaces",
        table_name="environments",
        postgresql_where=sa.text("replaces_environment_id IS NOT NULL"),
    )
    op.drop_column("environments", "replaces_environment_id")
