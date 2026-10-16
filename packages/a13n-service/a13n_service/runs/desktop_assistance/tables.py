"""Durable assistance and provider-target authority, independent of worker lifetime."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class DesktopTargetRow(Base):
    __tablename__ = "desktop_targets"
    __table_args__ = (CheckConstraint("inflight >= 0", name="inflight"),)
    # Hash of the native target identity, shared by aliases and provider-account rows.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder_id: Mapped[str | None] = mapped_column(String(72))
    epoch: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    inflight: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # An arbitrary command may leave a detached writer. No automatic quiescence claim follows it.
    unmanaged_input: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class DesktopAssistanceRow(Base):
    __tablename__ = "desktop_assistance_requests"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["workspace_id", "environment_id"], ["environments.workspace_id", "environments.id"]),
        UniqueConstraint("run_id", "tool_call_id"),
        CheckConstraint(
            "state IN ('preparing', 'awaiting_user', 'claiming', 'human_control', 'revoking', 'ready_to_resume', 'consumed', 'blocked', 'abandoned')",
            name="state",
        ),
        Index("ix_desktop_assistance_due", "deadline", "state"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    run_id: Mapped[str]
    tool_call_id: Mapped[str]
    environment_id: Mapped[str]
    target_key: Mapped[str] = mapped_column(ForeignKey("desktop_targets.key"))
    mount: Mapped[str]
    reason: Mapped[str]
    state: Mapped[str]
    generation: Mapped[str | None]
    epoch: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    controller_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    controller_session: Mapped[str | None]
    grant_id: Mapped[str | None]
    # Endpoint grants expire independently; this bounds the entire assistance wait.
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Fences provider I/O; expired claims reconcile the same grant, never issue GUI input.
    operation_id: Mapped[str | None]
    operation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    failure: Mapped[str | None]
    successor_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    observed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DesktopViewerRow(Base):
    __tablename__ = "desktop_viewers"
    __table_args__ = (Index("ix_desktop_viewers_assistance", "assistance_id"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    assistance_id: Mapped[str] = mapped_column(ForeignKey("desktop_assistance_requests.id"))
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    session_id: Mapped[str]
    generation: Mapped[str]
    epoch: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
