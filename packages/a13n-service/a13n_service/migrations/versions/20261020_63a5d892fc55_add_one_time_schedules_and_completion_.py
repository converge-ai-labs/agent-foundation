"""add one-time schedules and completion state

Revision ID: 63a5d892fc55
Revises: 33e2cc388478
"""

import sqlalchemy as sa
from alembic import op

revision = "63a5d892fc55"
down_revision = "33e2cc388478"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("schedule_type", sa.String(), server_default="cron", nullable=False))
    op.add_column("schedules", sa.Column("run_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("schedules", "cron_expression", existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column("schedules", "timezone", existing_type=sa.VARCHAR(), nullable=True)
    op.create_check_constraint(
        op.f("ck_schedules_completed"),
        "schedules",
        "state <> 'completed' OR (schedule_type = 'once' AND last_processed_fire_at IS NOT NULL AND last_processed_fire_at >= run_at)",
    )
    op.create_check_constraint(
        op.f("ck_schedules_time_rule"),
        "schedules",
        "(schedule_type = 'cron' AND cron_expression IS NOT NULL AND timezone IS NOT NULL AND run_at IS NULL) OR (schedule_type = 'once' AND cron_expression IS NULL AND timezone IS NULL AND run_at IS NOT NULL)",
    )

    op.drop_constraint(op.f("ck_schedules_state"), "schedules", type_="check")
    op.create_check_constraint(
        op.f("ck_schedules_state"), "schedules", "state IN ('enabled', 'paused', 'completed', 'archived')"
    )
    op.drop_constraint(op.f("ck_schedules_next_fire"), "schedules", type_="check")
    op.create_check_constraint(
        op.f("ck_schedules_next_fire"),
        "schedules",
        "(state <> 'enabled' AND next_fire_at IS NULL) OR (state = 'enabled' AND (next_fire_at IS NOT NULL OR (schedule_type = 'once' AND last_processed_fire_at IS NOT NULL AND last_processed_fire_at >= run_at)))",
    )
    op.execute("""
CREATE OR REPLACE FUNCTION guard_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.state = 'archived' OR
                   ROW(NEW.session_id, NEW.principal_id, NEW.authority, NEW.create_request_key, NEW.create_request_digest)
                   IS DISTINCT FROM
                   ROW(OLD.session_id, OLD.principal_id, OLD.authority, OLD.create_request_key, OLD.create_request_digest)
                THEN RAISE EXCEPTION 'schedule identity, authority and archived state are immutable'; END IF;
                IF OLD.state = 'completed' AND NEW.state NOT IN ('completed', 'archived')
                THEN RAISE EXCEPTION 'completed schedules cannot restart'; END IF;
                IF OLD.last_processed_fire_at IS NOT NULL AND
                   (NEW.last_processed_fire_at IS NULL OR NEW.last_processed_fire_at < OLD.last_processed_fire_at)
                THEN RAISE EXCEPTION 'schedule time cannot move backwards'; END IF;
                RETURN NEW;
            END $$
    """)


def downgrade() -> None:
    # Once rows have no cron equivalent. Refuse data loss; use a forward repair after adoption.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM schedules WHERE schedule_type = 'once')
            THEN RAISE EXCEPTION 'cannot downgrade while one-time schedules exist'; END IF;
        END $$
    """)
    op.drop_constraint(op.f("ck_schedules_state"), "schedules", type_="check")
    op.create_check_constraint(op.f("ck_schedules_state"), "schedules", "state IN ('enabled', 'paused', 'archived')")
    op.drop_constraint(op.f("ck_schedules_next_fire"), "schedules", type_="check")
    op.create_check_constraint(
        op.f("ck_schedules_next_fire"), "schedules", "(state = 'enabled') = (next_fire_at IS NOT NULL)"
    )
    op.execute("""
CREATE OR REPLACE FUNCTION guard_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.state = 'archived' OR
                   ROW(NEW.session_id, NEW.principal_id, NEW.authority, NEW.create_request_key, NEW.create_request_digest)
                   IS DISTINCT FROM
                   ROW(OLD.session_id, OLD.principal_id, OLD.authority, OLD.create_request_key, OLD.create_request_digest)
                THEN RAISE EXCEPTION 'schedule identity, authority and archived state are immutable'; END IF;
                IF OLD.last_processed_fire_at IS NOT NULL AND
                   (NEW.last_processed_fire_at IS NULL OR NEW.last_processed_fire_at < OLD.last_processed_fire_at)
                THEN RAISE EXCEPTION 'schedule time cannot move backwards'; END IF;
                RETURN NEW;
            END $$
    """)
    op.drop_constraint(op.f("ck_schedules_time_rule"), "schedules", type_="check")
    op.drop_constraint(op.f("ck_schedules_completed"), "schedules", type_="check")
    op.alter_column("schedules", "timezone", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("schedules", "cron_expression", existing_type=sa.VARCHAR(), nullable=False)
    op.drop_column("schedules", "run_at")
    op.drop_column("schedules", "schedule_type")
