"""record occurrence completion

Revision ID: 9cc1d8d6a9c0
Revises: 63a5d892fc55
"""

import sqlalchemy as sa
from alembic import op

revision = "9cc1d8d6a9c0"
down_revision = "63a5d892fc55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedule_occurrences", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("schedule_occurrences", sa.Column("completion_run_id", sa.String(length=72), nullable=True))
    op.add_column("schedule_occurrences", sa.Column("execution_outcome", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_schedule_occurrences_completion_run",
        "schedule_occurrences",
        "runs",
        ["thread_id", "completion_run_id"],
        ["thread_id", "id"],
    )
    op.create_check_constraint(
        op.f("ck_schedule_occurrences_completion"),
        "schedule_occurrences",
        "(status = 'completed' AND completed_at IS NOT NULL AND completion_run_id IS NOT NULL AND execution_outcome IS NOT NULL AND execution_outcome IN ('succeeded', 'failed', 'cancelled') AND completed_at >= finished_at) OR (status <> 'completed' AND completed_at IS NULL AND completion_run_id IS NULL AND execution_outcome IS NULL)",
    )

    op.drop_constraint(op.f("ck_schedule_occurrences_status"), "schedule_occurrences", type_="check")
    op.create_check_constraint(
        op.f("ck_schedule_occurrences_status"),
        "schedule_occurrences",
        "status IN ('pending', 'submitted', 'completed', 'skipped', 'failed', 'cancelled')",
    )
    op.drop_constraint(op.f("ck_schedule_occurrences_submission"), "schedule_occurrences", type_="check")
    op.create_check_constraint(
        op.f("ck_schedule_occurrences_submission"),
        "schedule_occurrences",
        "(status IN ('submitted', 'completed') AND thread_id IS NOT NULL AND entry_id IS NOT NULL AND reason IS NULL) "
        "OR (status NOT IN ('submitted', 'completed') AND thread_id IS NULL AND entry_id IS NULL)",
    )
    op.execute("""
CREATE OR REPLACE FUNCTION guard_schedule_occurrence() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF (OLD.status <> 'pending' AND NOT (OLD.status = 'submitted' AND NEW.status = 'completed')) OR
                   ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.schedule_id, NEW.session_id,
                       NEW.trigger, NEW.scheduled_at, NEW.covered_from_at, NEW.schedule_version, NEW.snapshot,
                       NEW.principal_id, NEW.authority, NEW.requested_by_id, NEW.request_key, NEW.request_digest,
                       NEW.deadline_at, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.schedule_id, OLD.session_id,
                       OLD.trigger, OLD.scheduled_at, OLD.covered_from_at, OLD.schedule_version, OLD.snapshot,
                       OLD.principal_id, OLD.authority, OLD.requested_by_id, OLD.request_key, OLD.request_digest,
                       OLD.deadline_at, OLD.created_at)
                THEN RAISE EXCEPTION 'occurrence intent and terminal states are immutable'; END IF;
                IF NEW.status = 'completed' AND OLD.status <> 'submitted'
                THEN RAISE EXCEPTION 'only submitted occurrences can complete'; END IF;
                IF OLD.status = 'submitted' AND
                   ROW(NEW.thread_id, NEW.entry_id, NEW.finished_at, NEW.reason, NEW.dispatch_attempts,
                       NEW.next_attempt_at, NEW.last_error)
                   IS DISTINCT FROM
                   ROW(OLD.thread_id, OLD.entry_id, OLD.finished_at, OLD.reason, OLD.dispatch_attempts,
                       OLD.next_attempt_at, OLD.last_error)
                THEN RAISE EXCEPTION 'submitted occurrence evidence is immutable'; END IF;
                RETURN NEW;
            END $$
    """)


def downgrade() -> None:
    # Completion evidence cannot be represented by the old schema. Refuse a lossy rollback.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM schedule_occurrences WHERE status = 'completed')
        THEN RAISE EXCEPTION 'cannot downgrade while completed occurrences exist'; END IF;
    END $$""")
    op.drop_constraint(op.f("ck_schedule_occurrences_status"), "schedule_occurrences", type_="check")
    op.create_check_constraint(
        op.f("ck_schedule_occurrences_status"),
        "schedule_occurrences",
        "status IN ('pending', 'submitted', 'skipped', 'failed', 'cancelled')",
    )
    op.drop_constraint(op.f("ck_schedule_occurrences_submission"), "schedule_occurrences", type_="check")
    op.create_check_constraint(
        op.f("ck_schedule_occurrences_submission"),
        "schedule_occurrences",
        "(status = 'submitted' AND thread_id IS NOT NULL AND entry_id IS NOT NULL AND reason IS NULL) "
        "OR (status <> 'submitted' AND thread_id IS NULL AND entry_id IS NULL)",
    )
    op.execute("""
CREATE OR REPLACE FUNCTION guard_schedule_occurrence() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.status <> 'pending' OR
                   ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.schedule_id, NEW.session_id,
                       NEW.trigger, NEW.scheduled_at, NEW.covered_from_at, NEW.schedule_version, NEW.snapshot,
                       NEW.principal_id, NEW.authority, NEW.requested_by_id, NEW.request_key, NEW.request_digest,
                       NEW.deadline_at, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.schedule_id, OLD.session_id,
                       OLD.trigger, OLD.scheduled_at, OLD.covered_from_at, OLD.schedule_version, OLD.snapshot,
                       OLD.principal_id, OLD.authority, OLD.requested_by_id, OLD.request_key, OLD.request_digest,
                       OLD.deadline_at, OLD.created_at)
                THEN RAISE EXCEPTION 'occurrence intent and terminal states are immutable'; END IF;
                RETURN NEW;
            END $$
    """)
    op.drop_constraint(op.f("ck_schedule_occurrences_completion"), "schedule_occurrences", type_="check")
    op.drop_constraint("fk_schedule_occurrences_completion_run", "schedule_occurrences", type_="foreignkey")
    op.drop_column("schedule_occurrences", "execution_outcome")
    op.drop_column("schedule_occurrences", "completion_run_id")
    op.drop_column("schedule_occurrences", "completed_at")
