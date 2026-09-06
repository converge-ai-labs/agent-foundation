"""allow takeover outcome adoption without harness entry.

Revision ID: 9c02613b20c0
Revises: 90c70b278335
Create Date: 2026-09-06 00:32:46.061111+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c02613b20c0"
down_revision: str | Sequence[str] | None = "90c70b278335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_run_attempts_harness_lifecycle_valid"
_PRIOR = (
    "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
    "OR (status IN ('running', 'succeeded') AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
    "OR (status IN ('yielded', 'failed', 'cancelled') "
    "AND ((harness_run_id IS NULL AND started_at IS NULL) "
    "OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))"
)
_ADOPTION = (
    "(status = 'succeeded' AND harness_run_id IS NULL AND started_at IS NULL "
    "AND replaces_run_attempt_id IS NOT NULL AND attempt_number >= 2)"
)


def upgrade() -> None:
    """Expand the check without changing columns, rows, or terminal guards."""
    _replace_constraint(f"{_PRIOR} OR {_ADOPTION}")


def downgrade() -> None:
    """Never rewrite immutable adoption history to satisfy the older constraint."""
    if op.get_bind().execute(sa.text(f"SELECT 1 FROM run_attempts WHERE {_ADOPTION} LIMIT 1")).first():
        raise RuntimeError("cannot downgrade after outcome adoption; retain the expanded schema and forward-repair")
    _replace_constraint(_PRIOR)


def _replace_constraint(expression: str) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        op.drop_constraint(op.f(_CONSTRAINT), "run_attempts", type_="check")
        op.create_check_constraint(op.f(_CONSTRAINT), "run_attempts", expression)
        return

    # SQLite must rebuild the referenced table. Keep copying and trigger restoration
    # atomic, and restore FK enforcement before returning to the migration runner.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                with op.batch_alter_table("run_attempts") as batch:
                    batch.drop_constraint(op.f(_CONSTRAINT), type_="check")
                    batch.create_check_constraint(op.f(_CONSTRAINT), expression)
                op.execute(
                    """
                    CREATE TRIGGER reject_terminal_run_attempt_update
                    BEFORE UPDATE ON run_attempts
                    WHEN OLD.status IN ('succeeded', 'yielded', 'failed', 'cancelled')
                    BEGIN
                        SELECT RAISE(ABORT, 'terminal RunAttempt rows are immutable');
                    END
                    """
                )
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("RunAttempt constraint migration found an invalid foreign key")
                connection.exec_driver_sql("COMMIT")
            except BaseException:
                connection.exec_driver_sql("ROLLBACK")
                raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
