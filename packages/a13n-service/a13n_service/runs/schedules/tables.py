"""Schedules keep configuration and scan progress; Occurrences retain submission and completion evidence."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules, trigger


class ScheduleRow(Stamped, Base):
    __tablename__ = "schedules"
    KIND: ClassVar[str] = "schedule"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "id", "session_id"),
        UniqueConstraint("workspace_id", "created_by_id", "create_request_key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "session_id"], ["sessions.workspace_id", "sessions.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "thread_id"],
            ["threads.workspace_id", "threads.session_id", "threads.id"],
            name="fk_schedules_target_thread",
        ),
        *(
            ForeignKeyConstraint(
                ["id", pointer],
                ["schedule_occurrences.schedule_id", "schedule_occurrences.id"],
                name=f"fk_schedules_{pointer}",
                use_alter=True,
                deferrable=True,
                initially="DEFERRED",
            )
            for pointer in ("pending_occurrence_id", "last_occurrence_id")
        ),
        CheckConstraint("state IN ('enabled', 'paused', 'completed', 'archived')", name="state"),
        CheckConstraint(
            "(state <> 'enabled' AND next_fire_at IS NULL) OR "
            "(state = 'enabled' AND (next_fire_at IS NOT NULL OR "
            "(schedule_type = 'once' AND last_processed_fire_at IS NOT NULL "
            "AND last_processed_fire_at >= run_at)))",
            name="next_fire",
        ),
        CheckConstraint(
            "(schedule_type = 'cron' AND cron_expression IS NOT NULL AND timezone IS NOT NULL AND run_at IS NULL) OR "
            "(schedule_type = 'once' AND cron_expression IS NULL AND timezone IS NULL AND run_at IS NOT NULL)",
            name="time_rule",
        ),
        CheckConstraint(
            "state <> 'completed' OR (schedule_type = 'once' AND last_processed_fire_at IS NOT NULL "
            "AND last_processed_fire_at >= run_at)",
            name="completed",
        ),
        CheckConstraint("(state = 'paused') = (pause_reason IS NOT NULL)", name="pause_reason"),
        CheckConstraint("(state = 'archived') = (archived_at IS NOT NULL)", name="archived"),
        CheckConstraint("max_lateness_seconds BETWEEN 60 AND 86400", name="lateness"),
        Index("ix_schedules_session_id", "session_id"),
        Index("ix_schedules_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_schedules_due", "next_fire_at", "id", postgresql_where=text("state = 'enabled'")),
        rules(
            identity_guarded("schedules"),
            """
            CREATE FUNCTION guard_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.state = 'archived' OR
                   ROW(NEW.principal_id, NEW.authority, NEW.create_request_key, NEW.create_request_digest)
                   IS DISTINCT FROM
                   ROW(OLD.principal_id, OLD.authority, OLD.create_request_key, OLD.create_request_digest)
                THEN RAISE EXCEPTION 'schedule identity, authority and archived state are immutable'; END IF;
                IF OLD.state = 'completed' AND NEW.state NOT IN ('completed', 'archived')
                THEN RAISE EXCEPTION 'completed schedules cannot restart'; END IF;
                IF OLD.last_processed_fire_at IS NOT NULL AND
                   (NEW.last_processed_fire_at IS NULL OR NEW.last_processed_fire_at < OLD.last_processed_fire_at)
                THEN RAISE EXCEPTION 'schedule time cannot move backwards'; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("schedules", "guard_schedule"),
            unversioned=("next_fire_at", "last_processed_fire_at", "pending_occurrence_id", "last_occurrence_id"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    session_id: Mapped[str]
    thread_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str]
    description: Mapped[str | None]
    labels: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str]
    pause_reason: Mapped[str | None]
    schedule_type: Mapped[str] = mapped_column(default="cron", server_default="cron")
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cron_expression: Mapped[str | None]
    timezone: Mapped[str | None]
    max_lateness_seconds: Mapped[int]
    invocation: Mapped[dict] = mapped_column(JSONB)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_request_key: Mapped[str]
    create_request_digest: Mapped[str]
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_processed_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_occurrence_id: Mapped[str | None] = mapped_column(String(72))
    last_occurrence_id: Mapped[str | None] = mapped_column(String(72))


class OccurrenceRow(Stamped, Base):
    __tablename__ = "schedule_occurrences"
    KIND: ClassVar[str] = "schedule_occurrence"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "session_id"], ["sessions.workspace_id", "sessions.id"]),
        UniqueConstraint("schedule_id", "id"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "session_id", "id"),
        UniqueConstraint("entry_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "schedule_id"],
            ["schedules.workspace_id", "schedules.id"],
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "thread_id"],
            ["threads.workspace_id", "threads.session_id", "threads.id"],
            name="fk_schedule_occurrences_thread",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(["thread_id", "entry_id"], ["inbox_entries.thread_id", "inbox_entries.id"]),
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="trigger"),
        CheckConstraint(
            "status IN ('pending', 'submitted', 'completed', 'skipped', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "(trigger = 'scheduled' AND scheduled_at IS NOT NULL AND covered_from_at IS NOT NULL "
            "AND covered_from_at <= scheduled_at AND requested_by_id IS NULL AND request_key IS NULL AND request_digest IS NULL) "
            "OR (trigger = 'manual' AND scheduled_at IS NULL AND covered_from_at IS NULL "
            "AND requested_by_id IS NOT NULL AND request_key IS NOT NULL AND request_digest IS NOT NULL)",
            name="trigger_fields",
        ),
        CheckConstraint(
            "(status IN ('submitted', 'completed') AND thread_id IS NOT NULL AND entry_id IS NOT NULL AND reason IS NULL) "
            "OR (status NOT IN ('submitted', 'completed') AND thread_id IS NULL AND entry_id IS NULL)",
            name="submission",
        ),
        CheckConstraint("(status = 'pending') = (finished_at IS NULL)", name="finished"),
        CheckConstraint("dispatch_attempts >= 0", name="attempts"),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND completion_run_id IS NOT NULL "
            "AND execution_outcome IS NOT NULL AND execution_outcome IN ('succeeded', 'failed', 'cancelled') "
            "AND completed_at >= finished_at) OR "
            "(status <> 'completed' AND completed_at IS NULL AND completion_run_id IS NULL AND execution_outcome IS NULL)",
            name="completion",
        ),
        ForeignKeyConstraint(
            ["thread_id", "completion_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_schedule_occurrences_completion_run",
        ),
        Index(
            "uq_schedule_occurrences_due",
            "schedule_id",
            "scheduled_at",
            unique=True,
            postgresql_where=text("trigger = 'scheduled'"),
        ),
        Index(
            "uq_schedule_occurrences_manual",
            "workspace_id",
            "requested_by_id",
            "request_key",
            unique=True,
            postgresql_where=text("trigger = 'manual'"),
        ),
        Index(
            "uq_schedule_occurrences_pending", "schedule_id", unique=True, postgresql_where=text("status = 'pending'")
        ),
        Index("ix_schedule_occurrences_session", "workspace_id", "session_id", "created_at", "id"),
        Index("ix_schedule_occurrences_history", "schedule_id", "created_at", "id"),
        Index(
            "ix_schedule_occurrences_pending",
            text("LEAST(next_attempt_at, deadline_at)"),
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        rules(
            """
            CREATE FUNCTION guard_schedule_occurrence() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF (OLD.status <> 'pending' AND NOT (OLD.status = 'submitted' AND NEW.status = 'completed')) OR
                   ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.schedule_id,
                       NEW.trigger, NEW.scheduled_at, NEW.covered_from_at, NEW.schedule_version, NEW.snapshot,
                       NEW.principal_id, NEW.authority, NEW.requested_by_id, NEW.request_key, NEW.request_digest,
                       NEW.deadline_at, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.schedule_id,
                       OLD.trigger, OLD.scheduled_at, OLD.covered_from_at, OLD.schedule_version, OLD.snapshot,
                       OLD.principal_id, OLD.authority, OLD.requested_by_id, OLD.request_key, OLD.request_digest,
                       OLD.deadline_at, OLD.created_at)
                THEN RAISE EXCEPTION 'occurrence intent and terminal states are immutable'; END IF;
                IF NEW.status = 'completed' AND OLD.status <> 'submitted'
                THEN RAISE EXCEPTION 'only submitted occurrences can complete'; END IF;
                IF OLD.status = 'submitted' AND
                   ROW(NEW.session_id, NEW.thread_id, NEW.entry_id, NEW.finished_at, NEW.reason, NEW.dispatch_attempts,
                       NEW.next_attempt_at, NEW.last_error)
                   IS DISTINCT FROM
                   ROW(OLD.session_id, OLD.thread_id, OLD.entry_id, OLD.finished_at, OLD.reason, OLD.dispatch_attempts,
                       OLD.next_attempt_at, OLD.last_error)
                THEN RAISE EXCEPTION 'submitted occurrence evidence is immutable'; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("schedule_occurrences", "guard_schedule_occurrence"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    schedule_id: Mapped[str]
    session_id: Mapped[str]
    trigger: Mapped[str]
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    covered_from_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_version: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSONB)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    requested_by_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    request_key: Mapped[str | None]
    request_digest: Mapped[str | None]
    status: Mapped[str]
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatch_attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    reason: Mapped[str | None]
    thread_id: Mapped[str | None] = mapped_column(String(72))
    entry_id: Mapped[str | None] = mapped_column(String(72))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_run_id: Mapped[str | None] = mapped_column(String(72))
    execution_outcome: Mapped[str | None]
