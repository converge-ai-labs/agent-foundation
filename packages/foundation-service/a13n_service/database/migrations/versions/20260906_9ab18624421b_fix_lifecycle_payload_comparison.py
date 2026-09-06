"""fix lifecycle payload comparison.

Revision ID: 9ab18624421b
Revises: 9c02613b20c0
Create Date: 2026-09-06 03:40:28.931170+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9ab18624421b"
down_revision: str | Sequence[str] | None = "9c02613b20c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow projection updates without weakening immutable JSON storage."""
    _replace_fact_guard(payload_cast="::text")


def downgrade() -> None:
    """Restore the previous function; no stored data is changed."""
    _replace_fact_guard(payload_cast="")


def _replace_fact_guard(*, payload_cast: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    fact_columns = (
        "seq",
        "id",
        "tenant_id",
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
    comparisons = []
    for column in fact_columns:
        cast = payload_cast if column == "payload" else ""
        comparisons.append(f"NEW.{column}{cast} IS DISTINCT FROM OLD.{column}{cast}")
    changed = " OR ".join(comparisons)
    # JSON has no equality operator. Text comparison also protects its original
    # representation, unlike a JSONB cast that normalizes whitespace and keys.
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
