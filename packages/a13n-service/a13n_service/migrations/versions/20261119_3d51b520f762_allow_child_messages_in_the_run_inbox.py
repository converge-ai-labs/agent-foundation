"""allow child messages in the run inbox

Revision ID: 3d51b520f762
Revises: 9d1cfd9c1a34
"""

import sqlalchemy as sa
from alembic import op

revision = "3d51b520f762"
down_revision = "9d1cfd9c1a34"
branch_labels = None
depends_on = None


_CHECKS = (
    ("inbox_entries", "kind", "'message', 'child_result', 'tool_result', 'browser_handback', 'desktop_handback'"),
    ("inbox_entries", "request_kind", "'thread', 'message', 'fork'"),
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
            values = (
                values.replace("'child_result'", "'child_result', 'child_message'")
                if column != "request_kind"
                else values + ", 'child_message'"
            )
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
            f"ADD CONSTRAINT {name} CHECK ({column} IN ({values}))" + (" NOT VALID" if add_message else "")
        )


def upgrade() -> None:
    # Alembic does not autogenerate CHECK edits. Existing rows met the stricter checks, so no table scan is needed.
    # Preflight both locks without queuing behind live traffic. Failure rolls back the whole revision.
    op.execute("LOCK TABLE inbox_entries, runs IN ACCESS EXCLUSIVE MODE NOWAIT")
    _replace_checks(add_message=True)


def downgrade() -> None:
    # Fence writes before checking whether the previous schema can represent the stored data.
    op.execute("LOCK TABLE inbox_entries, runs IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM inbox_entries WHERE kind = 'child_message' OR request_kind = 'child_message')
            OR EXISTS (SELECT 1 FROM runs WHERE trigger = 'child_message')
        """)
    ):
        raise RuntimeError("Cannot downgrade while child messages are stored")
    _replace_checks(add_message=False)
