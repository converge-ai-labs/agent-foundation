"""One durable organization lifecycle per Memory Space."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class SpaceOrganizationRow(Base):
    __tablename__ = "memory_space_organizations"
    __table_args__ = (Index("ix_memory_space_organizations_due", "due_at"),)
    space_id: Mapped[str] = mapped_column(ForeignKey("memory_spaces.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"), unique=True)
    # An explicit manual request; automatic work is discovered from sequence/configuration changes.
    pending: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    source_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    observed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    processed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    configuration_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    history_gap: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    failure: Mapped[dict | None] = mapped_column(JSONB)
