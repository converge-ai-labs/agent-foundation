"""Persisted schedules and confirmation/delivery fencing; Runs own execution status."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.connectivity.domain import JsonObject
from a13n_service.database import Base


class RoutineRecord(Base):
    __tablename__ = "bot_routines"
    __table_args__ = (
        Index("ix_bot_routines_due", "state", "next_run_at"),
        Index("ix_bot_routines_channel", "account_id", "conversation_id", "id"),
        Index("ix_bot_routines_card_due", "card_available_at"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(72), ForeignKey("application_accounts.id", ondelete="CASCADE"))
    source_run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"))
    account_version: Mapped[int] = mapped_column(Integer)
    target_version: Mapped[int | None] = mapped_column(Integer)
    native_context_json: Mapped[JsonObject] = mapped_column(JSON)
    conversation_id: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(24))
    definition_json: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    proposal_json: Mapped[JsonObject | None] = mapped_column(JSON(none_as_null=True))
    proposal_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    action_token: Mapped[str] = mapped_column(String(64))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("runs.id", ondelete="SET NULL"))
    last_error: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(256))
    card_version: Mapped[int] = mapped_column(Integer, default=0)
    card_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_lease_token: Mapped[str | None] = mapped_column(String(64))
    card_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    card_attempts: Mapped[int] = mapped_column(Integer, default=0)
    card_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventSourceRecord(Base):
    """Confirmed source configuration and activation boundary for an event task."""

    __tablename__ = "bot_routine_event_sources"
    __table_args__ = (Index("ix_bot_routine_event_sources_account", "account_id", "routine_id"),)
    routine_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("bot_routines.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(String(72))
    account_version: Mapped[int] = mapped_column(Integer)
    target_id: Mapped[str] = mapped_column(String(72))
    target_version: Mapped[int] = mapped_column(Integer)
    generation: Mapped[str] = mapped_column(String(64))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventOccurrenceRecord(Base):
    """Durable event acceptance; retained keys prevent webhook redelivery duplicates."""

    __tablename__ = "bot_routine_event_occurrences"
    __table_args__ = (
        Index("ix_bot_routine_event_occurrences_pending", "routine_id", "generation", "state", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    routine_id: Mapped[str] = mapped_column(String(72), ForeignKey("bot_routines.id", ondelete="CASCADE"))
    generation: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    event_json: Mapped[JsonObject] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
