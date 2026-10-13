"""Durable, coalesced organization opportunities for a session's dedicated file memory."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class SessionMemoryRow(Base):
    __tablename__ = "session_memories"
    __table_args__ = (
        Index("ix_session_memories_pending", "due_at", postgresql_where=text("pending OR active_run_id IS NOT NULL")),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    # Settings belong to Session; these are execution delegation and maintenance progress.
    authority: Mapped[dict | None] = mapped_column(JSONB)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("agent_revisions.id"))
    pending: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    source_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    processed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    failure: Mapped[dict | None] = mapped_column(JSONB)
