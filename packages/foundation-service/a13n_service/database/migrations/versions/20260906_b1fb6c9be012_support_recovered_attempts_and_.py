"""support recovered attempts and immutable lifecycle facts.

Revision ID: b1fb6c9be012
Revises: 90c70b278335
Create Date: 2026-09-06 08:34:39.256010+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1fb6c9be012"
down_revision: str | Sequence[str] | None = "90c70b278335"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHECK_NAME = "ck_run_attempts_harness_lifecycle_valid"
_PREVIOUS_CHECK = (
    "(status = 'leased' AND harness_run_id IS NULL AND started_at IS NULL) "
    "OR (status IN ('running', 'succeeded') AND harness_run_id IS NOT NULL AND started_at IS NOT NULL) "
    "OR (status IN ('yielded', 'failed', 'cancelled') "
    "AND ((harness_run_id IS NULL AND started_at IS NULL) "
    "OR (harness_run_id IS NOT NULL AND started_at IS NOT NULL)))"
)
_CURRENT_CHECK = _PREVIOUS_CHECK + (
    " OR (status = 'succeeded' AND harness_run_id IS NULL AND started_at IS NULL "
    "AND replaces_run_attempt_id IS NOT NULL AND attempt_number >= 2)"
)


def upgrade() -> None:
    """Allow a replacement to seal an existing candidate without entering Harness."""
    _replace_attempt_check(_CURRENT_CHECK)
    if op.get_bind().dialect.name == "postgresql":
        _replace_fact_guard(compare_json_text=True)


def downgrade() -> None:
    """Refuse rollback once durable recovery facts require the expanded contract."""
    _replace_attempt_check(_PREVIOUS_CHECK, downgrade=True)
    if op.get_bind().dialect.name == "postgresql":
        _replace_fact_guard(compare_json_text=False)


def _replace_attempt_check(condition: str, *, downgrade: bool = False) -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Serialize the downgrade eligibility check with all Attempt writers.
        op.execute("LOCK TABLE run_attempts IN ACCESS EXCLUSIVE MODE")
        _require_safe_downgrade(downgrade)
        op.drop_constraint(op.f(_CHECK_NAME), "run_attempts", type_="check")
        op.create_check_constraint(op.f(_CHECK_NAME), "run_attempts", condition)
        return
    # SQLite needs a table rebuild with incoming/self foreign keys temporarily
    # disabled. One explicit write transaction preserves data, indexes and guards;
    # a retry after commit but before Alembic stamping safely repeats the rebuild.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            _require_safe_downgrade(downgrade)
            with op.batch_alter_table("run_attempts") as batch:
                batch.drop_constraint(op.f(_CHECK_NAME), type_="check")
                batch.create_check_constraint(op.f(_CHECK_NAME), condition)
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
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("RunAttempt migration found invalid foreign keys")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _require_safe_downgrade(downgrade: bool) -> None:
    if downgrade and op.get_bind().scalar(
        sa.text("SELECT 1 FROM run_attempts WHERE status = 'succeeded' AND harness_run_id IS NULL LIMIT 1")
    ):
        raise RuntimeError("Recovered Attempt facts prevent downgrade; retain this schema and roll forward")


def _replace_fact_guard(*, compare_json_text: bool) -> None:
    fact_columns = (
        "seq",
        "id",
        "organization_id",
        "entity_type",
        "entity_id",
        "resource_seq",
        "entity_version",
        "event_type",
        "schema_version",
        "mutation_id",
        "session_id",
        "thread_id",
        "run_id",
        "run_attempt_id",
        "payload",
        "actor_type",
        "actor_id",
        "occurred_at",
        "created_at",
    )
    changed = " OR ".join(
        f"NEW.{column}::text IS DISTINCT FROM OLD.{column}::text"
        if column == "payload" and compare_json_text
        else f"NEW.{column} IS DISTINCT FROM OLD.{column}"
        for column in fact_columns
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION reject_lifecycle_fact_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF {changed} THEN
                RAISE EXCEPTION 'lifecycle fact columns are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
