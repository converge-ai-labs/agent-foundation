"""index workspace run start times for usage queries

Revision ID: 48a66dd56558
Revises: a9a9c6399e27

Additive index only: old and new readers remain compatible, with no row rewrite
or backfill. Concurrent creation permits normal Run writes while scanning history;
the migration runner bounds lock waits and build time. An interrupted build is
repaired on retry, including a valid index committed before the revision stamp.
"""

import sqlalchemy as sa
from alembic import op

revision = "48a66dd56558"
down_revision = "a9a9c6399e27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('ix_runs_workspace_started')")
        )
        if valid is False:
            op.drop_index("ix_runs_workspace_started", postgresql_concurrently=True)
        op.create_index(
            "ix_runs_workspace_started",
            "runs",
            ["workspace_id", "started_at"],
            unique=False,
            postgresql_where=sa.text("started_at IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    # Keep a rejected downgrade atomic across older revisions. This takes a brief
    # table lock under the runner's lock timeout, but does not scan or rewrite rows.
    op.drop_index("ix_runs_workspace_started", table_name="runs")
