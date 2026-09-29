"""freeze initial thread model history

Revision ID: d55be8968c94
Revises: f8ec2b5e2171
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d55be8968c94"
down_revision = "f8ec2b5e2171"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "message_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_threads_imported_history"), "threads", "origin = 'new' OR message_history = '[]'::jsonb"
    )
    op.execute("""
        CREATE FUNCTION guard_thread_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.message_history IS DISTINCT FROM OLD.message_history
            THEN RAISE EXCEPTION 'thread initial history is immutable'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER guard_thread_history BEFORE UPDATE ON threads FOR EACH ROW EXECUTE FUNCTION guard_thread_history()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_thread_history ON threads")
    op.execute("DROP FUNCTION guard_thread_history()")
    op.drop_constraint(op.f("ck_threads_imported_history"), "threads", type_="check")
    op.drop_column("threads", "message_history")
