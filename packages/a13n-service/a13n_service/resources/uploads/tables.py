"""A staged upload: the workspace binding and idempotency evidence of bytes stored under its own key."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, immutable, rules


class UploadRow(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        # A writer's request key names one upload; a concurrent repeat loses here and replays the winner.
        UniqueConstraint("workspace_id", "created_by_id", "request_key"),
        CheckConstraint("size >= 0", name="size"),
        rules(immutable("uploads")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    request_key: Mapped[str]
    filename: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
