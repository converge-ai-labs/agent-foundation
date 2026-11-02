"""retain Browser profiles, idle policies and shared download identity

Revision ID: ed5de523fc26
Revises: 29270d0290da
"""

import sqlalchemy as sa
from alembic import op

revision = "ed5de523fc26"
down_revision = "29270d0290da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_browsers_status"), "browsers", type_="check")
    op.create_check_constraint(
        op.f("ck_browsers_status"),
        "browsers",
        "status IN ('reserved','creating','ready','stopping','stopped','starting','deleting','deleted','lost')",
    )
    op.add_column(
        "browsers", sa.Column("start_dispatched", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    op.add_column("browsers", sa.Column("runtime_id", sa.String(), nullable=True))
    op.add_column("browsers", sa.Column("stop_after_seconds", sa.Integer(), nullable=True))
    op.add_column("browsers", sa.Column("delete_after_seconds", sa.Integer(), nullable=True))
    op.create_table(
        "browser_downloads",
        sa.Column("browser_id", sa.String(length=72), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("guid", sa.String(length=256), nullable=False),
        sa.Column("upload_id", sa.String(length=72), nullable=False),
        sa.ForeignKeyConstraint(["browser_id"], ["browsers.id"], name=op.f("fk_browser_downloads_browser_id_browsers")),
        sa.ForeignKeyConstraint(["upload_id"], ["uploads.id"], name=op.f("fk_browser_downloads_upload_id_uploads")),
        sa.PrimaryKeyConstraint("browser_id", "generation", "guid", name=op.f("pk_browser_downloads")),
    )


def downgrade() -> None:
    # Old workers cannot preserve retained runtimes or their idle policies.
    op.create_check_constraint(
        op.f("ck_browsers_rollback_retention"),
        "browsers",
        "status IN ('deleted','lost') OR (runtime_id IS NULL AND NOT start_dispatched "
        "AND stop_after_seconds IS NULL AND delete_after_seconds IS NULL "
        "AND status NOT IN ('stopping','stopped','starting'))",
    )
    op.drop_constraint(op.f("ck_browsers_status"), "browsers", type_="check")
    op.create_check_constraint(
        op.f("ck_browsers_status"),
        "browsers",
        "status IN ('reserved','creating','ready','deleting','deleted','lost')",
    )
    op.drop_constraint(op.f("ck_browsers_rollback_retention"), "browsers", type_="check")
    op.drop_table("browser_downloads")
    op.drop_column("browsers", "delete_after_seconds")
    op.drop_column("browsers", "stop_after_seconds")
    op.drop_column("browsers", "runtime_id")
    op.drop_column("browsers", "start_dispatched")
