"""One durable organization lifecycle per Memory Space."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class OrganizationRow(Base):
    """Accepted inputs and resolved target; Run owns all execution state."""

    __tablename__ = "memory_organizations"
    KIND: ClassVar[str] = "memory_organization"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("ix_memory_organizations_workspace_created", "workspace_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    request: Mapped[dict] = mapped_column(JSONB)
    sources: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Historical identities survive deletion of the target, like a Run's frozen mounts.
    memory_id: Mapped[str]
    space_id: Mapped[str | None]
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), unique=True)
    # Populated in the same admission transaction, after the target has been frozen.
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), unique=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SpaceOrganizationRow(Base):
    __tablename__ = "memory_space_organizations"
    __table_args__ = (Index("ix_memory_space_organizations_due", "due_at"),)
    space_id: Mapped[str] = mapped_column(ForeignKey("memory_spaces.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"), unique=True)
    # An explicit manual request; automatic work is discovered from sequence/configuration changes.
    pending: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_change_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    observed_change_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    active_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    source_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    observed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    processed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    configuration_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    history_gap: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    failure: Mapped[dict | None] = mapped_column(JSONB)
