"""Per-object publication and collection fences; business rows own references."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class ObjectPublicationRecord(Base):
    __tablename__ = "object_publications"
    __table_args__ = (
        CheckConstraint("phase IN ('publishing', 'ready', 'collecting', 'collected')", name="phase_valid"),
        CheckConstraint("(phase IN ('publishing', 'collecting')) = (lease_expires_at IS NOT NULL)", name="lease_valid"),
        Index("ix_object_publications_collection", "updated_at", "key"),
    )

    key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    generation: Mapped[str] = mapped_column(String(72), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(512))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
