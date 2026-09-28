"""align deferred wait categories

Revision ID: b70fad768c94
Revises: 4290f39fdea4
"""

from alembic import op

revision = "b70fad768c94"
down_revision = "4290f39fdea4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema only: this pre-release contract does not convert existing business data.
    op.drop_constraint(op.f("ck_runs_wait_reason"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_wait_reason"), "runs", "wait_reason IN ('approval', 'call', 'multiple')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_runs_wait_reason"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_wait_reason"), "runs", "wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')"
    )
