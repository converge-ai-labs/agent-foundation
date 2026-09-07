"""preserve immutable lifecycle JSON facts during projection.

Revision ID: b1fb6c9be012
Revises: 0d5f1ae47708
Create Date: 2026-09-06 08:34:39.256010+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b1fb6c9be012"
down_revision: str | Sequence[str] | None = "0d5f1ae47708"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Permit projection updates without changing immutable JSON fact bytes."""
    if op.get_bind().dialect.name == "postgresql":
        _replace_fact_guard(compare_json_text=True)


def downgrade() -> None:
    """Restore the prior guard; no durable rows or lifecycle constraints change."""
    if op.get_bind().dialect.name == "postgresql":
        _replace_fact_guard(compare_json_text=False)


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
