"""One immutable source event and its mutable delivery receipt."""

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

from a13n_service.infra.db import Base, Stamped, rules, trigger

_GUARD = """
CREATE FUNCTION guard_connector_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.provider_id, NEW.source_id,
           NEW.remote_id, NEW.account_id, NEW.user_id, NEW.event_type, NEW.occurred_at, NEW.digest, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.provider_id, OLD.source_id,
           OLD.remote_id, OLD.account_id, OLD.user_id, OLD.event_type, OLD.occurred_at, OLD.digest, OLD.created_at)
       OR (NEW.payload IS NOT NULL AND NEW.payload IS DISTINCT FROM OLD.payload)
       OR (OLD.snapshot IS NOT NULL AND NEW.snapshot IS DISTINCT FROM OLD.snapshot)
       OR (OLD.trigger_id IS NOT NULL AND NEW.trigger_id IS DISTINCT FROM OLD.trigger_id)
       OR (OLD.inbox_id IS NOT NULL AND ROW(NEW.state, NEW.inbox_id, NEW.thread_id)
           IS DISTINCT FROM ROW(OLD.state, OLD.inbox_id, OLD.thread_id))
    THEN RAISE EXCEPTION 'connector event source, snapshot and committed receipt are immutable'; END IF;
    RETURN NEW;
END $$
"""


class EventRow(Stamped, Base):
    __tablename__ = "connector_events"
    KIND: ClassVar[str] = "connector_event"
    __table_args__ = (
        UniqueConstraint("provider_id", "source_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["connector_providers.workspace_id", "connector_providers.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "trigger_id"], ["connector_triggers.workspace_id", "connector_triggers.id"]
        ),
        CheckConstraint("state IN ('unmatched', 'pending', 'stored', 'ignored', 'submitted', 'failed')", name="state"),
        CheckConstraint("(state <> 'submitted') OR (inbox_id IS NOT NULL AND thread_id IS NOT NULL)", name="receipt"),
        Index("ix_connector_events_due", "due_at", "id", postgresql_where=text("state IN ('pending', 'unmatched')")),
        Index(
            "ix_connector_events_backlog",
            "provider_id",
            "state",
            postgresql_where=text("state IN ('pending', 'unmatched')"),
        ),
        Index("ix_connector_events_workspace", "workspace_id", "created_at", "id"),
        Index("ix_connector_events_trigger", "trigger_id", "created_at", "id"),
        Index(
            "ix_connector_events_retention",
            "created_at",
            postgresql_where=text("state NOT IN ('pending', 'unmatched')"),
        ),
        rules(_GUARD, trigger("connector_events", "guard_connector_event")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str]
    workspace_id: Mapped[str]
    provider_id: Mapped[str]
    source_id: Mapped[str] = mapped_column(String(256))
    remote_id: Mapped[str] = mapped_column(String(256))
    account_id: Mapped[str] = mapped_column(String(256))
    user_id: Mapped[str] = mapped_column(String(256))
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    digest: Mapped[str] = mapped_column(String(64))
    trigger_id: Mapped[str | None]
    snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    state: Mapped[str]
    attempts: Mapped[int] = mapped_column(default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failure: Mapped[str | None]
    inbox_id: Mapped[str | None] = mapped_column(ForeignKey("inbox_entries.id"))
    thread_id: Mapped[str | None] = mapped_column(ForeignKey("threads.id"))
