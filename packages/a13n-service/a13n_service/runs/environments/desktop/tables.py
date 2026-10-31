"""Native-target control, Computer dispatch evidence, and actual blocked recipients."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, rules


class DesktopControlRow(Stamped, Base):
    __tablename__ = "desktop_controls"
    __table_args__ = (
        UniqueConstraint("target_key"),
        CheckConstraint("control IN ('automation','draining','human','returning')", name="control"),
        CheckConstraint("generation > 0 AND episode >= 0", name="generation"),
        CheckConstraint("(claim_id IS NULL) = (claim_until IS NULL)", name="claim"),
        Index("ix_desktop_controls_due", "due_at", postgresql_where=text("control <> 'automation'")),
        rules(unversioned=("due_at", "claim_id", "claim_until")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    # Native identity, shared even across Environment/provider-account aliases.
    target_key: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(BigInteger, default=1)
    episode: Mapped[int] = mapped_column(BigInteger, default=0)
    control: Mapped[str] = mapped_column(default="automation")
    environment_id: Mapped[str | None] = mapped_column(ForeignKey("environments.id"))
    provider_identity: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    controller_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    controller_thread_id: Mapped[str | None] = mapped_column(ForeignKey("threads.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guest_generation: Mapped[str | None]
    grant_id: Mapped[str | None]
    # Once true, even an expired worker claim must revoke this identity before reopening.
    grant_dispatched: Mapped[bool] = mapped_column(default=False)
    viewer: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    claim_id: Mapped[str | None]
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[str | None]


class ComputerOperationRow(Base):
    __tablename__ = "computer_operations"
    __table_args__ = (
        Index("ix_computer_operations_unsettled", "control_id", postgresql_where=text("settled_at IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("desktop_controls.id"))
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    attempt_id: Mapped[str] = mapped_column(ForeignKey("run_attempts.id"))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("tool_executions.id"))
    generation: Mapped[int]
    episode: Mapped[int]
    action: Mapped[str]
    # Null is unknown after worker loss; no lease deadline clears it.
    dispatched: Mapped[bool | None]
    effect: Mapped[str | None]
    cleanup_complete: Mapped[bool | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DesktopBlockedRow(Base):
    __tablename__ = "desktop_blocked_threads"
    __table_args__ = (UniqueConstraint("control_id", "generation", "episode", "thread_id"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("desktop_controls.id"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    # The originating frozen mount, not a later Thread selection.
    mount: Mapped[dict] = mapped_column(JSONB)
    generation: Mapped[int]
    episode: Mapped[int]
    suppressed: Mapped[bool] = mapped_column(default=False)
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbox_entries.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DesktopViewerRow(Base):
    __tablename__ = "desktop_readers"
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("desktop_controls.id"))
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"))
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    guest_generation: Mapped[str | None]
    episode: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
