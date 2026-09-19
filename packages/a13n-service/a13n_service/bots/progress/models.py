"""Durable delivery coordinates; Run remains the execution state authority."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class ProgressRecord(Base):
    __tablename__ = "bot_progress"
    __table_args__ = (
        Index("ix_bot_progress_due", "done", "available_at"),
        Index("ix_bot_progress_account_id", "account_id"),
    )

    run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("application_accounts.id", ondelete="CASCADE"), nullable=False
    )
    account_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reply_in_thread: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(256), nullable=False)
    requester_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    message_id: Mapped[str | None] = mapped_column(String(256))
    rendered_status: Mapped[str | None] = mapped_column(String(32))
    replies_json: Mapped[list[str] | None] = mapped_column(JSON)
    rendered_reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
