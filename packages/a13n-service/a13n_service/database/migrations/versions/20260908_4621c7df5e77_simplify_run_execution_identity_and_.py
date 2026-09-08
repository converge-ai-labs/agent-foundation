"""simplify Run execution identity and budgets.

Revision ID: 4621c7df5e77
Revises: 8560ee8dfdab
Create Date: 2026-09-08 06:02:12.803496+00:00
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4621c7df5e77"
down_revision: str | Sequence[str] | None = "8560ee8dfdab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BUDGET_COLUMNS = (
    ("recovery_policy_version", "execution_policy_version"),
    ("max_recovery_attempts", "max_attempts"),
    ("recovery_deadline_at", "execution_deadline_at"),
    ("recovery_attempts_started", "attempts_charged"),
)


def upgrade() -> None:
    """Consolidate equivalent facts with all execution processes stopped.

    PostgreSQL scans existing rows and rebuilds indexes under bounded DDL locks.
    SQLite replaces tables in one explicit transaction with FK enforcement paused
    on this migration connection only, then checks every FK before committing.
    Existing version-1 object checkpoints need a separate migration or fresh installation;
    old and new workers cannot run against the same schema.
    """
    connection = op.get_bind()
    for query in (
        "SELECT 1 FROM run_attempts WHERE fence <> attempt_number OR claimed_at <> created_at LIMIT 1",
        "SELECT 1 FROM runs WHERE next_attempt_fence <> attempts_started + 1 LIMIT 1",
        "SELECT 1 FROM run_attempts GROUP BY worker_id HAVING count(DISTINCT worker_generation) > 1 LIMIT 1",
    ):
        if connection.scalar(sa.text(query)) is not None:
            raise RuntimeError("Run identity facts differ; reconcile them before consolidation")
    _with_schema_guards(_upgrade)


def _upgrade() -> None:
    with op.batch_alter_table("child_run_relationships") as batch:
        batch.drop_constraint("fk_child_run_relationships_parent_attempt", type_="foreignkey")
    with op.batch_alter_table("run_attempts") as batch:
        batch.drop_index("uq_run_attempts_fence_identity")
        batch.drop_index("uq_run_attempts_fence")
        batch.alter_column("attempt_number", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)
        batch.alter_column("recovery_reason", new_column_name="start_reason")
        batch.drop_constraint(op.f("ck_run_attempts_fence_positive"), type_="check")
        batch.drop_constraint(op.f("ck_run_attempts_worker_generation_bounded"), type_="check")
        batch.drop_column("worker_generation")
        batch.drop_column("fence")
        batch.drop_column("claimed_at")
        batch.create_index(
            "uq_run_attempts_fence_identity", ["organization_id", "run_id", "id", "attempt_number"], unique=True
        )
    _create_parent_attempt_fk("attempt_number")
    with op.batch_alter_table("runs") as batch:
        _replace_budget_checks(batch, upgrading=True)
        for old, new in _BUDGET_COLUMNS:
            batch.alter_column(old, new_column_name=new)
        batch.drop_constraint(op.f("ck_runs_next_attempt_fence_positive"), type_="check")
        batch.drop_column("next_attempt_fence")


def downgrade() -> None:
    """Restore derived facts offline; object checkpoints are not rewritten.

    A forward repair is preferred after new executions have started. Older
    revisions may reject new lifecycle states and must not be forced through.
    """
    _with_schema_guards(_downgrade)


def _downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        _replace_budget_checks(batch, upgrading=False)
        for old, new in _BUDGET_COLUMNS:
            batch.alter_column(new, new_column_name=old)
        batch.add_column(sa.Column("next_attempt_fence", sa.BigInteger(), nullable=True))
    op.execute("UPDATE runs SET next_attempt_fence = attempts_started + 1")
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("next_attempt_fence", nullable=False)
        batch.create_check_constraint(op.f("ck_runs_next_attempt_fence_positive"), "next_attempt_fence >= 1")
    with op.batch_alter_table("child_run_relationships") as batch:
        batch.drop_constraint("fk_child_run_relationships_parent_attempt", type_="foreignkey")
    with op.batch_alter_table("run_attempts") as batch:
        batch.drop_index("uq_run_attempts_fence_identity")
        batch.add_column(sa.Column("fence", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("worker_generation", sa.String(256), nullable=True))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE run_attempts SET fence = attempt_number, worker_generation = worker_id, claimed_at = created_at")
    with op.batch_alter_table("run_attempts") as batch:
        for column in ("fence", "worker_generation", "claimed_at"):
            batch.alter_column(column, nullable=False)
        batch.alter_column("start_reason", new_column_name="recovery_reason")
        batch.alter_column("attempt_number", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
        batch.create_check_constraint(op.f("ck_run_attempts_fence_positive"), "fence >= 1")
        batch.create_check_constraint(
            op.f("ck_run_attempts_worker_generation_bounded"), "length(worker_generation) BETWEEN 1 AND 256"
        )
        batch.create_index("uq_run_attempts_fence", ["organization_id", "run_id", "fence"], unique=True)
        batch.create_index("uq_run_attempts_fence_identity", ["organization_id", "run_id", "id", "fence"], unique=True)
    _create_parent_attempt_fk("fence")


def _create_parent_attempt_fk(column: str) -> None:
    with op.batch_alter_table("child_run_relationships") as batch:
        batch.create_foreign_key(
            "fk_child_run_relationships_parent_attempt",
            "run_attempts",
            ["organization_id", "parent_run_id", "parent_run_attempt_id", "parent_run_attempt_fence"],
            ["organization_id", "run_id", "id", column],
            ondelete="RESTRICT",
        )


def _with_schema_guards(migrate: Callable[[], None]) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        # Preserve immutable history while allowing only this transactional backfill.
        op.execute("ALTER TABLE runs DISABLE TRIGGER reject_sealed_run_update")
        op.execute("ALTER TABLE run_attempts DISABLE TRIGGER reject_terminal_run_attempt_update")
        migrate()
        op.execute("ALTER TABLE runs ENABLE TRIGGER reject_sealed_run_update")
        op.execute("ALTER TABLE run_attempts ENABLE TRIGGER reject_terminal_run_attempt_update")
        return
    triggers = list(connection.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")))
    # PRAGMA foreign_keys cannot change within a transaction. This connection
    # belongs only to the migration runner and is disposed after the operation.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    # Explicit BEGIN includes DDL and Alembic's following revision stamp in the
    # same transaction even with sqlite3's legacy transaction behavior.
    connection.exec_driver_sql("BEGIN")
    for name, _ in triggers:
        connection.exec_driver_sql("DROP TRIGGER " + connection.dialect.identifier_preparer.quote(name))
    migrate()
    for _, sql in triggers:
        connection.exec_driver_sql(sql)
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("Run schema migration would violate a foreign key")


def _replace_budget_checks(batch, *, upgrading: bool) -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    # Reflected SQLite checks retain old column names during batch renames.
    for name, expression in (
        (
            "recovery_values_non_negative",
            "max_attempts >= 0 AND max_handoffs >= 0 AND attempts_started >= 0 AND attempts_charged >= 0 AND handoffs_completed >= 0",
        ),
        (
            "recovery_counts_valid",
            "attempts_charged <= max_attempts AND handoffs_completed <= max_handoffs AND attempts_charged <= attempts_started AND attempts_started <= attempts_charged + handoffs_completed",
        ),
    ):
        batch.drop_constraint(op.f("ck_runs_" + name), type_="check")
        if not upgrading:
            for old, new in _BUDGET_COLUMNS:
                expression = expression.replace(new, old)
        batch.create_check_constraint(op.f("ck_runs_" + name), expression)
