"""Account-owned durable notification cursor and fenced scanner lease."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class GitHubPollRecord(Base):
    __tablename__ = "github_notification_polls"
    __table_args__ = (CheckConstraint("claim_generation >= 0", name="generation_nonnegative"),)
    account_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("application_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    cursor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    has_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claim_owner: Mapped[str | None] = mapped_column(String(72))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
