"""App-scoped connection ownership; never stores transport credentials."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class EventConnectionRecord(Base):
    __tablename__ = "connectivity_event_connections"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_versions: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    owner: Mapped[str] = mapped_column(String(72), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
