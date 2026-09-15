"""initialize lifecycle events.

Revision ID: 023eff74515b
Revises: 676ce43cf244
Create Date: 2026-09-04 08:22:36.975678+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "023eff74515b"
down_revision: str | Sequence[str] | None = "676ce43cf244"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "lifecycle_events",
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=72), nullable=False),
        sa.Column("resource_seq", sa.BigInteger(), nullable=False),
        sa.Column("entity_version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("mutation_id", sa.String(length=72), nullable=False),
        sa.Column("session_id", sa.String(length=72), nullable=True),
        sa.Column("thread_id", sa.String(length=72), nullable=True),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("run_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=72), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_state", sa.String(length=32), nullable=False),
        sa.Column("projection_attempts", sa.BigInteger(), nullable=False),
        sa.Column("projection_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projection_lease_owner", sa.String(length=128), nullable=True),
        sa.Column("projection_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projection_error_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "(entity_type = 'run' AND entity_id = run_id AND run_attempt_id IS NULL) OR (entity_type = 'run_attempt' AND entity_id = run_attempt_id AND run_attempt_id IS NOT NULL)",
            name=op.f("ck_lifecycle_events_entity_correlation_valid"),
        ),
        sa.CheckConstraint(
            "(projection_state = 'pending' AND projection_next_attempt_at IS NOT NULL AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL AND projected_at IS NULL) OR (projection_state = 'retry_wait' AND projection_next_attempt_at IS NOT NULL AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL AND projected_at IS NULL) OR (projection_state = 'projecting' AND projection_next_attempt_at IS NULL AND projection_lease_owner IS NOT NULL AND projection_lease_expires_at IS NOT NULL AND projected_at IS NULL) OR (projection_state = 'projected' AND projection_next_attempt_at IS NULL AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL AND projected_at IS NOT NULL) OR (projection_state = 'abandoned' AND projection_next_attempt_at IS NULL AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL AND projected_at IS NULL)",
            name=op.f("ck_lifecycle_events_projection_shape_valid"),
        ),
        sa.CheckConstraint("entity_type IN ('run', 'run_attempt')", name=op.f("ck_lifecycle_events_entity_type_valid")),
        sa.CheckConstraint(
            "event_type IN ('run.accepted', 'run.running', 'run.waiting', 'run.completed', 'run.failed', 'run.cancelled', 'run_attempt.leased', 'run_attempt.running', 'run_attempt.succeeded', 'run_attempt.yielded', 'run_attempt.failed', 'run_attempt.cancelled')",
            name=op.f("ck_lifecycle_events_event_type_valid"),
        ),
        sa.CheckConstraint("entity_version >= 1", name=op.f("ck_lifecycle_events_entity_version_positive")),
        sa.CheckConstraint("length(schema_version) >= 1", name=op.f("ck_lifecycle_events_schema_version_non_blank")),
        sa.CheckConstraint(
            "projection_attempts >= 0", name=op.f("ck_lifecycle_events_projection_attempts_non_negative")
        ),
        sa.CheckConstraint("resource_seq >= 1", name=op.f("ck_lifecycle_events_resource_seq_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "run_attempt_id"],
            ["run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"],
            name=op.f("fk_lifecycle_events_organization_id_run_attempts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name=op.f("fk_lifecycle_events_organization_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["sessions.organization_id", "sessions.id"],
            name=op.f("fk_lifecycle_events_organization_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_lifecycle_events")),
        sa.UniqueConstraint("id", name="uq_lifecycle_events_id"),
        sa.UniqueConstraint(
            "organization_id", "entity_type", "entity_id", "resource_seq", name="uq_lifecycle_events_resource_seq"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "mutation_id",
            "event_type",
            "entity_type",
            "entity_id",
            name="uq_lifecycle_events_mutation_fact",
        ),
    )
    op.create_index(
        "ix_lifecycle_events_attempt", "lifecycle_events", ["organization_id", "run_attempt_id", "seq"], unique=False
    )
    op.create_index(
        "ix_lifecycle_events_projection_due",
        "lifecycle_events",
        ["projection_state", "projection_next_attempt_at", "seq"],
        unique=False,
        postgresql_where=sa.text("projection_state IN ('pending', 'projecting', 'retry_wait')"),
    )
    op.create_index(
        "ix_lifecycle_events_resource",
        "lifecycle_events",
        ["organization_id", "entity_type", "entity_id", "seq"],
        unique=False,
    )
    op.create_index("ix_lifecycle_events_run", "lifecycle_events", ["organization_id", "run_id", "seq"], unique=False)
    op.create_index(
        "ix_lifecycle_events_organization_seq", "lifecycle_events", ["organization_id", "seq"], unique=False
    )
    _create_fact_immutability_trigger()


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    _drop_fact_immutability_trigger()
    op.drop_index("ix_lifecycle_events_organization_seq", table_name="lifecycle_events")
    op.drop_index("ix_lifecycle_events_run", table_name="lifecycle_events")
    op.drop_index("ix_lifecycle_events_resource", table_name="lifecycle_events")
    op.drop_index(
        "ix_lifecycle_events_projection_due",
        table_name="lifecycle_events",
        postgresql_where=sa.text("projection_state IN ('pending', 'projecting', 'retry_wait')"),
    )
    op.drop_index("ix_lifecycle_events_attempt", table_name="lifecycle_events")
    op.drop_table("lifecycle_events")


def _create_fact_immutability_trigger() -> None:
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
    changed = " OR ".join(f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in fact_columns)
    op.execute(
        f"""
        CREATE FUNCTION reject_lifecycle_fact_update()
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
    op.execute(
        """
        CREATE TRIGGER reject_lifecycle_fact_update
        BEFORE UPDATE ON lifecycle_events
        FOR EACH ROW EXECUTE FUNCTION reject_lifecycle_fact_update()
        """
    )


def _drop_fact_immutability_trigger() -> None:
    op.execute("DROP TRIGGER reject_lifecycle_fact_update ON lifecycle_events")
    op.execute("DROP FUNCTION reject_lifecycle_fact_update()")
