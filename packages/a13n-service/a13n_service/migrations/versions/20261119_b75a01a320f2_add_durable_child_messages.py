"""add durable child messages

Revision ID: b75a01a320f2
Revises: 9d1cfd9c1a34
"""

import sqlalchemy as sa
from alembic import op

revision = "b75a01a320f2"
down_revision = "9d1cfd9c1a34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "child_messages",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("child_run_id", sa.String(length=72), nullable=False),
        sa.Column("tool_call_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parent_thread_id", sa.String(length=72), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disposition", sa.String(), nullable=True),
        sa.CheckConstraint(
            "disposition IN ('appended', 'expired', 'suppressed')", name=op.f("ck_child_messages_disposition")
        ),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 8", name=op.f("ck_child_messages_ordinal")),
        sa.ForeignKeyConstraint(
            ["child_run_id"], ["runs.id"], name=op.f("fk_child_messages_child_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_child_messages")),
        sa.UniqueConstraint("child_run_id", "ordinal", name=op.f("uq_child_messages_child_run_id_ordinal")),
        sa.UniqueConstraint("child_run_id", "tool_call_id", name=op.f("uq_child_messages_child_run_id_tool_call_id")),
    )
    op.create_index(
        "ix_child_messages_expiry",
        "child_messages",
        ["deadline", "id"],
        unique=False,
        postgresql_where=sa.text("disposition IS NULL OR disposition = 'appended'"),
    )
    # Broader CHECKs do not need to scan rows the previous CHECKs already covered.
    _replace_checks(add_message=True)


_CHECKS = (
    (
        "outbox",
        "kind",
        "'webhook', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback', 'email', 'memory_purge', 'checkpoint_cleanup'",
    ),
    ("inbox_entries", "kind", "'message', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback'"),
    (
        "runs",
        "trigger",
        "'input', 'queued', 'resume', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback', 'spawned'",
    ),
)


def _replace_checks(*, add_message: bool) -> None:
    for table, column, values in _CHECKS:
        name = f"ck_{table}_{column}"
        if add_message:
            values = values.replace("'child_result'", "'child_result', 'child_message'")
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
            f"ADD CONSTRAINT {name} CHECK ({column} IN ({values}))" + (" NOT VALID" if add_message else "")
        )


def downgrade() -> None:
    # Fence concurrent writes before testing whether the previous schema can represent the stored data.
    op.execute("LOCK TABLE child_messages, outbox, inbox_entries, runs IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM child_messages)
            OR EXISTS (SELECT 1 FROM outbox WHERE kind = 'child_message')
            OR EXISTS (SELECT 1 FROM inbox_entries WHERE kind = 'child_message')
            OR EXISTS (SELECT 1 FROM runs WHERE trigger = 'child_message')
        """)
    ):
        raise RuntimeError("Cannot downgrade after child messages have been admitted")
    _replace_checks(add_message=False)
    op.drop_table("child_messages")
