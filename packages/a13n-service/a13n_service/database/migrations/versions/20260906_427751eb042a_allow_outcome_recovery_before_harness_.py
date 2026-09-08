"""allow outcome recovery before Harness entry.

Revision ID: 427751eb042a
Revises: b1fb6c9be012
Create Date: 2026-09-06 13:38:52.714606+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "427751eb042a"
down_revision: str | Sequence[str] | None = "b1fb6c9be012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Permit an authorized successor to seal an existing outcome without Harness entry.

    This replaces one check and scans run_attempts without rewriting rows. PostgreSQL
    holds its table DDL lock until commit; apply under the migration runner's bounded
    lock/statement timeouts. No backfill is needed. Older workers remain valid writers,
    but readers must be upgraded before enabling recovery-only successful Attempts.
    Alembic's by-name comparator does not detect changes to an existing check body.
    """
    with op.batch_alter_table("run_attempts") as batch_op:
        batch_op.drop_constraint(op.f("ck_run_attempts_harness_lifecycle_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_run_attempts_harness_lifecycle_valid"),
            "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
            "OR (status = 'running' AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
            "OR (status IN ('succeeded', 'yielded', 'failed', 'cancelled') "
            "AND ((harness_run_id IS NULL AND started_at IS NULL) "
            "OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))",
        )
    _restore_sqlite_terminal_guard()


def downgrade() -> None:
    """Restore the old check only if no recovery-only success has been persisted.

    Existing incompatible rows intentionally fail validation and roll back the
    transaction. Retain the expanded check and roll forward in that case; never
    invent Harness identities or discard historical Attempts to force a downgrade.
    """
    with op.batch_alter_table("run_attempts") as batch_op:
        batch_op.drop_constraint(op.f("ck_run_attempts_harness_lifecycle_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_run_attempts_harness_lifecycle_valid"),
            "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
            "OR (status IN ('running', 'succeeded') AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
            "OR (status IN ('yielded', 'failed', 'cancelled') "
            "AND ((harness_run_id IS NULL AND started_at IS NULL) "
            "OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))",
        )
    _restore_sqlite_terminal_guard()


def _restore_sqlite_terminal_guard() -> None:
    # SQLite's batch replacement drops table-owned triggers. PostgreSQL changes
    # the check in place and retains its trigger and all referenced identities.
    if op.get_bind().dialect.name == "sqlite":
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
