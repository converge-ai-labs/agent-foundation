"""Last installation/conversation observation, never a permanent access grant."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.durable_operations.models import EntityRequestKey


class BotCheckRecord(Base):
    __tablename__ = "bot_checks"

    account_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("application_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    # Empty target denotes the installation check, not a conversation identifier.
    conversation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BotTestRecord(EntityRequestKey, Base):
    """One user-sent setup probe; references survive ingress replay retention."""

    __tablename__ = "bot_tests"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "account_version >= 1 AND credential_generation >= 1 AND target_version >= 1", name="generation_valid"
        ),
        CheckConstraint("expires_at > created_at", name="deadline_valid"),
        Index("ix_bot_tests_account_created", "account_id", "created_at", "id"),
        Index("ix_bot_tests_batch", "batch_id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settings_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[str] = mapped_column(String(72), nullable=False)
    target_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_target_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admission_id: Mapped[str | None] = mapped_column(String(72))
    batch_id: Mapped[str | None] = mapped_column(String(72))
    binding_id: Mapped[str | None] = mapped_column(String(72))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(String(72))
    steer_id: Mapped[str | None] = mapped_column(String(72))
    rejection_code: Mapped[str | None] = mapped_column(String(128))


class BotReplyRecord(Base):
    """Provider evidence for one native reply; never a resend queue or transcript."""

    __tablename__ = "bot_replies"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("binding_id", "organization_id", "workspace_id"),
            ("agent_thread_bindings.id", "agent_thread_bindings.organization_id", "agent_thread_bindings.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id", "run_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("provider_key IN ('slack', 'lark', 'github')", name="provider_valid"),
        CheckConstraint("status IN ('dispatching', 'succeeded', 'rejected', 'outcome_unknown')", name="status_valid"),
        CheckConstraint("(status = 'dispatching') = (finished_at IS NULL)", name="completion_valid"),
        CheckConstraint("(status = 'succeeded') = (receipt_json IS NOT NULL)", name="receipt_valid"),
        CheckConstraint("account_version >= 1 AND credential_generation >= 1", name="generation_valid"),
        Index("ix_bot_replies_account_started", "account_id", "started_at", "id"),
        Index("ix_bot_replies_run_started", "run_id", "started_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_id: Mapped[str] = mapped_column(String(72), nullable=False)
    binding_id: Mapped[str] = mapped_column(String(72), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(72))
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    test_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("bot_tests.id", ondelete="SET NULL"), index=True)
    run_attempt_id: Mapped[str] = mapped_column(String(72), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(16), nullable=False)
    account_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settings_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
